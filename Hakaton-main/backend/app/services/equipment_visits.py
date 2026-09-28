"""Durable tracker lifecycle ingestion: one smoothed visit per camera/session/raw track interval."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import utcnow
from app.models import Camera, EquipmentEvent, EquipmentVisit, new_id
from app.schemas import EquipmentEventAck, EquipmentEventIn

settings = get_settings()


class EquipmentVisitError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass
class _Change:
    changed: bool = False
    boundary: str | None = None


def _payload_sha256(payload: EquipmentEventIn) -> str:
    normalized = payload.model_dump(mode="json")
    for field in ("observed_at", "first_seen_at", "last_seen_at"):
        normalized[field] = getattr(payload, field).astimezone(UTC).isoformat()
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _duration(first_seen_at: datetime, last_seen_at: datetime) -> float:
    return max(0.0, (last_seen_at - first_seen_at).total_seconds())


def _update_latest(visit: EquipmentVisit, payload: EquipmentEventIn) -> bool:
    """Advance the producer-confirmed observation only; late outbox messages cannot move it backwards."""
    if payload.last_seen_at <= visit.last_seen_at:
        return False
    visit.last_seen_at = payload.last_seen_at
    visit.duration_seconds = _duration(visit.first_seen_at, visit.last_seen_at)
    visit.equipment_class = payload.equipment_type
    visit.confidence = payload.confidence
    return True


def _update_message_time(visit: EquipmentVisit, payload: EquipmentEventIn) -> bool:
    if payload.observed_at <= visit.last_message_at:
        return False
    visit.last_message_at = payload.observed_at
    return True


def _is_timeout_lost(visit: EquipmentVisit) -> bool:
    return visit.status == "lost" and visit.close_reason == "observation_timeout"


async def _visit(session: AsyncSession, payload: EquipmentEventIn) -> EquipmentVisit | None:
    return await session.scalar(
        select(EquipmentVisit).where(
            EquipmentVisit.camera_id == payload.camera_id,
            EquipmentVisit.tracker_session_id == str(payload.tracker_session_id),
            EquipmentVisit.track_id == payload.track_id,
            EquipmentVisit.first_seen_at == payload.first_seen_at,
        )
    )


def _new_visit(camera: Camera, payload: EquipmentEventIn, received_at: datetime) -> EquipmentVisit:
    return EquipmentVisit(
        id=new_id("visit"),
        site_id=camera.site_id,
        camera_id=camera.id,
        tracker_session_id=str(payload.tracker_session_id),
        track_id=payload.track_id,
        equipment_class=payload.equipment_type,
        first_seen_at=payload.first_seen_at,
        last_seen_at=payload.last_seen_at,
        duration_seconds=_duration(payload.first_seen_at, payload.last_seen_at),
        status="active",
        confidence=payload.confidence,
        last_message_at=payload.observed_at,
        received_at=received_at,
        closed_at=None,
        close_reason=None,
        has_observation_gap=False,
    )


def _apply_terminal(visit: EquipmentVisit, payload: EquipmentEventIn) -> _Change:
    """Apply a producer terminal fact without silently replacing an already accepted terminal fact."""
    is_last = payload.event_type == "LAST_SEEN"
    wanted = "completed" if is_last else "lost"
    boundary = "disappeared" if is_last else None
    reason = "not_observed_after_grace" if is_last else (payload.reason or "camera_interrupted")

    if visit.status in {"completed", "lost"} and not _is_timeout_lost(visit):
        if visit.status != wanted:
            raise EquipmentVisitError(409, "terminal_event_conflict")
        if payload.last_seen_at > visit.last_seen_at:
            raise EquipmentVisitError(409, "visit_time_conflict")
        return _Change()

    changed = _update_latest(visit, payload)
    changed = _update_message_time(visit, payload) or changed
    if visit.status != wanted or visit.closed_at != (payload.last_seen_at if is_last else payload.observed_at):
        changed = True
    visit.status = wanted
    visit.closed_at = payload.last_seen_at if is_last else payload.observed_at
    visit.close_reason = reason
    if payload.confidence is not None and payload.last_seen_at >= visit.last_seen_at:
        visit.confidence = payload.confidence
    return _Change(changed=changed, boundary=boundary)


async def _ingest_equipment_event(session: AsyncSession, payload: EquipmentEventIn) -> EquipmentEventAck:
    """Atomically update a visit and, for a lifecycle boundary, persist its audit event.

    Camera row locking serializes same-camera writers in PostgreSQL. SQLite gets its single writer transaction; model
    uniqueness remains the final guard for racing interval creation.
    """
    now = utcnow()
    if payload.observed_at > now + timedelta(minutes=1):
        raise EquipmentVisitError(422, "observed_at is more than one minute in the future")
    event_id = str(payload.event_id)
    payload_sha = _payload_sha256(payload)
    boundary_kind = {"FIRST_SEEN": "appeared", "LAST_SEEN": "disappeared"}.get(payload.event_type)

    async with session.begin():
        camera = await session.scalar(select(Camera).where(Camera.id == payload.camera_id).with_for_update(of=Camera))
        if camera is None:
            raise EquipmentVisitError(404, "Камера не найдена")

        if boundary_kind:
            existing = await session.get(EquipmentEvent, event_id)
            if existing is not None:
                if existing.payload_sha256 != payload_sha:
                    raise EquipmentVisitError(409, "event_id_conflict")
                return EquipmentEventAck(event_id=event_id, visit_id=existing.visit_id, duplicate=True)

        visit = await _visit(session, payload)
        if visit is None:
            if camera.deleted_at is not None:
                raise EquipmentVisitError(404, "Камера не найдена")
            visit = _new_visit(camera, payload, now)
            session.add(visit)
            changed = _Change(changed=True)
        else:
            changed = _Change()

        if payload.event_type == "FIRST_SEEN":
            # FIRST_SEEN may be delivered after LAST_SEEN. It records only the missing appearance and never reopens.
            if visit.status == "active":
                changed.changed = _update_latest(visit, payload) or changed.changed
                changed.changed = _update_message_time(visit, payload) or changed.changed
            changed.boundary = "appeared"
        elif payload.event_type == "PRESENT":
            cutoff = now - timedelta(seconds=settings.equipment_observation_timeout_seconds)
            if _is_timeout_lost(visit) and payload.last_seen_at > cutoff:
                visit.status, visit.closed_at, visit.close_reason = "active", None, None
                changed.changed = True
            if visit.status == "active":
                changed.changed = _update_latest(visit, payload) or changed.changed
                changed.changed = _update_message_time(visit, payload) or changed.changed
        else:
            terminal = _apply_terminal(visit, payload)
            changed.changed = terminal.changed or changed.changed
            changed.boundary = terminal.boundary

        if changed.boundary:
            previous = await session.scalar(
                select(EquipmentEvent.event_id).where(
                    EquipmentEvent.visit_id == visit.id,
                    EquipmentEvent.event_type == changed.boundary,
                )
            )
            if previous is None:
                session.add(
                    EquipmentEvent(
                        event_id=event_id,
                        site_id=visit.site_id,
                        camera_id=visit.camera_id,
                        tracker_session_id=visit.tracker_session_id,
                        track_id=visit.track_id,
                        visit_id=visit.id,
                        equipment_class=visit.equipment_class,
                        event_type=changed.boundary,
                        timestamp=payload.first_seen_at if changed.boundary == "appeared" else payload.last_seen_at,
                        confidence=payload.confidence,
                        received_at=now,
                        payload_sha256=payload_sha,
                    )
                )
                changed.changed = True

        await session.flush()
        return EquipmentEventAck(event_id=event_id, visit_id=visit.id, duplicate=not changed.changed)




async def ingest_equipment_event(session: AsyncSession, payload: EquipmentEventIn) -> EquipmentEventAck:
    """Ingest one producer message and resolve a final unique-key race to the winning durable result."""
    try:
        return await _ingest_equipment_event(session, payload)
    except IntegrityError:
        # The failed context has rolled back. A concurrent writer may have committed the same visit/event meanwhile;
        # resolve only to that persisted result, never report a partial local mutation as accepted.
        event_id = str(payload.event_id)
        payload_sha = _payload_sha256(payload)
        boundary = {"FIRST_SEEN": "appeared", "LAST_SEEN": "disappeared"}.get(payload.event_type)
        async with session.begin():
            if boundary:
                existing = await session.get(EquipmentEvent, event_id)
                if existing is not None:
                    if existing.payload_sha256 != payload_sha:
                        raise EquipmentVisitError(409, "event_id_conflict")
                    return EquipmentEventAck(event_id=event_id, visit_id=existing.visit_id, duplicate=True)
            visit = await _visit(session, payload)
            if visit is not None:
                if boundary:
                    saved = await session.scalar(
                        select(EquipmentEvent.event_id).where(
                            EquipmentEvent.visit_id == visit.id, EquipmentEvent.event_type == boundary
                        )
                    )
                    if saved is not None:
                        return EquipmentEventAck(event_id=event_id, visit_id=visit.id, duplicate=True)
                if payload.event_type == "PRESENT":
                    return EquipmentEventAck(event_id=event_id, visit_id=visit.id, duplicate=True)
        raise
async def mark_stale_visits(session: AsyncSession, now: datetime) -> int:
    """Mark only still-active stale intervals lost, with the freshness predicate in SQL to avoid heartbeat races."""
    cutoff = now - timedelta(seconds=settings.equipment_observation_timeout_seconds)
    result = await session.execute(
        update(EquipmentVisit)
        .where(EquipmentVisit.status == "active", EquipmentVisit.last_seen_at < cutoff)
        .values(
            status="lost",
            closed_at=now,
            close_reason="observation_timeout",
            has_observation_gap=True,
        )
    )
    await session.commit()
    return result.rowcount or 0
