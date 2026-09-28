from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


class StreamHub:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._subscribers: set[asyncio.Queue[str]] = set()

    def authorized(self, value: str | None) -> bool:
        prefix = "Bearer "
        return bool(value and value.startswith(prefix) and hmac.compare_digest(value[len(prefix):], self.api_key))

    def publish(self, message: dict[str, Any]) -> None:
        text = json.dumps(message, separators=(",", ":"), allow_nan=False)
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(text)

    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        if not self.authorized(ws.headers.get("authorization")):
            await ws.close(code=4401)
            return
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        try:
            while True:
                await ws.send_text(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            self._subscribers.discard(queue)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)
