from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from typing import Awaitable, Callable

from .tracking import EquipmentEvent

log = logging.getLogger(__name__)


class EventOutbox:
    def __init__(self, path: Path, max_events: int):
        self.path, self.max_events = path, max_events
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL)")
        self.db.commit()
        self._lock = asyncio.Lock()

    async def put(self, event: EquipmentEvent) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._put, event)

    def _put(self, event: EquipmentEvent) -> bool:
        count = self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if count >= self.max_events:
            log.error("event outbox full; event retained only in logs", extra={"camera_id": event.camera_id, "event_id": str(event.event_id)})
            return False
        self.db.execute("INSERT OR IGNORE INTO events(event_id,payload,next_at) VALUES (?, ?, ?)", (str(event.event_id), json.dumps(event.payload()), datetime.now(UTC).isoformat()))
        self.db.commit()
        return True

    async def drain(self, send: Callable[[dict], Awaitable[bool]], stop: asyncio.Event) -> None:
        while not stop.is_set():
            async with self._lock:
                item = await asyncio.to_thread(self._next)
            if item is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                continue
            event_id, payload, attempts = item
            try:
                accepted = await send(payload)
            except Exception:
                log.exception("equipment event delivery failed", extra={"event_id": event_id})
                accepted = False
            async with self._lock:
                if accepted:
                    await asyncio.to_thread(self._ack, event_id)
                else:
                    await asyncio.to_thread(self._retry, event_id, attempts)

    def _next(self) -> tuple[str, dict, int] | None:
        row = self.db.execute("SELECT event_id,payload,attempts FROM events WHERE next_at <= ? ORDER BY next_at LIMIT 1", (datetime.now(UTC).isoformat(),)).fetchone()
        return (row[0], json.loads(row[1]), row[2]) if row else None

    def _ack(self, event_id: str) -> None:
        self.db.execute("DELETE FROM events WHERE event_id=?", (event_id,))
        self.db.commit()

    def _retry(self, event_id: str, attempts: int) -> None:
        delay = min(300, 2 ** min(attempts + 1, 8))
        next_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        self.db.execute("UPDATE events SET attempts=?, next_at=? WHERE event_id=?", (attempts + 1, next_at, event_id))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
