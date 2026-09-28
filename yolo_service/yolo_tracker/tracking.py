from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
from typing import Iterable
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class CameraSpec:
    id: str
    site_id: str
    zone_kind: str
    rtsp_url: str
    demo_clip: str | None = None


@dataclass(frozen=True, slots=True)
class Detection:
    tracker_id: int
    equipment_type: str
    confidence: float
    xyxy: tuple[float, float, float, float]

    def percent_box(self, frame_w: int, frame_h: int) -> dict[str, float]:
        if frame_w <= 0 or frame_h <= 0:
            raise ValueError("frame dimensions must be positive")
        x1, y1, x2, y2 = self.xyxy
        if not all(math.isfinite(v) for v in self.xyxy):
            raise ValueError("box coordinates must be finite")
        left, right = sorted((max(0.0, min(x1, frame_w)), max(0.0, min(x2, frame_w))))
        top, bottom = sorted((max(0.0, min(y1, frame_h)), max(0.0, min(y2, frame_h))))
        return {
            "x": round(left * 100 / frame_w, 4),
            "y": round(top * 100 / frame_h, 4),
            "w": round((right - left) * 100 / frame_w, 4),
            "h": round((bottom - top) * 100 / frame_h, 4),
        }


@dataclass(slots=True)
class StableTrack:
    public_id: str
    raw_id: int
    types: deque[tuple[str, float]] = field(default_factory=lambda: deque(maxlen=12))
    first_observed: datetime | None = None
    last_observed: datetime | None = None
    confirmed: bool = False
    observations: int = 0
    last_heartbeat: datetime | None = None

    def observe(self, kind: str, confidence: float, at: datetime) -> None:
        self.types.append((kind, confidence))
        self.observations += 1
        self.first_observed = self.first_observed or at
        self.last_observed = at

    def voted_type(self) -> str:
        if not self.types:
            raise ValueError("track has no class observations")
        totals: Counter[str] = Counter()
        for item, score in self.types:
            totals[item] += score
        return totals.most_common(1)[0][0]

    def last_confidence_for(self, kind: str) -> float:
        for observed_kind, confidence in reversed(self.types):
            if observed_kind == kind:
                return confidence
        raise ValueError("voted type has no confidence")


@dataclass(frozen=True, slots=True)
class EquipmentEvent:
    event_id: UUID
    event_type: str
    camera_id: str
    tracker_session_id: UUID
    track_id: str
    equipment_type: str
    observed_at: datetime
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    reason: str | None = None
    model_version: str | None = None
    confidence: float | None = None


    def payload(self) -> dict[str, str | float]:
        result: dict[str, str | float] = {
            "event_id": str(self.event_id), "event_type": self.event_type, "camera_id": self.camera_id,
            "tracker_session_id": str(self.tracker_session_id), "track_id": self.track_id,
            "equipment_type": self.equipment_type, "observed_at": iso(self.observed_at),
        }
        for name, value in (("first_seen_at", self.first_seen_at), ("last_seen_at", self.last_seen_at)):
            if value is not None:
                result[name] = iso(value)
        if self.reason:
            result["reason"] = self.reason
        if self.model_version:
            result["model_version"] = self.model_version
        if self.confidence is not None:
            result["confidence"] = self.confidence
        return result


class VisitState:
    """Per-camera tracker state; a raw ByteTrack id never crosses a session boundary."""

    def __init__(self, camera_id: str, session_id: UUID, confirm_frames: int, grace_seconds: float, model_version: str):
        self.camera_id, self.session_id = camera_id, session_id
        self.confirm_frames, self.grace_seconds, self.model_version = confirm_frames, grace_seconds, model_version
        self.tracks: dict[int, StableTrack] = {}

    def _event(self, event_type: str, track: StableTrack, at: datetime, reason: str | None = None) -> EquipmentEvent:
        equipment_type = track.voted_type()
        return EquipmentEvent(
            event_id=uuid4(), event_type=event_type, camera_id=self.camera_id, tracker_session_id=self.session_id,
            track_id=track.public_id, equipment_type=equipment_type, observed_at=at,
            first_seen_at=track.first_observed, last_seen_at=track.last_observed, reason=reason, model_version=self.model_version,
            confidence=track.last_confidence_for(equipment_type),
        )

    def observe(self, detections: Iterable[Detection], at: datetime, heartbeat_seconds: float) -> list[EquipmentEvent]:
        events: list[EquipmentEvent] = []
        for detection in detections:
            track = self.tracks.get(detection.tracker_id)
            if track is None:
                track = StableTrack(public_id=f"s-{self.session_id}:{detection.tracker_id}", raw_id=detection.tracker_id)
                self.tracks[detection.tracker_id] = track
            track.observe(detection.equipment_type, detection.confidence, at)
            if not track.confirmed and track.observations >= self.confirm_frames:
                track.confirmed = True
                track.last_heartbeat = at
                events.append(self._event("FIRST_SEEN", track, at))
            elif track.confirmed and (track.last_heartbeat is None or (at - track.last_heartbeat).total_seconds() >= heartbeat_seconds):
                track.last_heartbeat = at
                events.append(self._event("PRESENT", track, at))
        return events

    def expire(self, at: datetime) -> list[EquipmentEvent]:
        expired = [track for track in self.tracks.values() if track.last_observed and (at - track.last_observed).total_seconds() > self.grace_seconds]
        events = [self._event("LAST_SEEN", track, track.last_observed) for track in expired if track.confirmed]
        for track in expired:
            self.tracks.pop(track.raw_id, None)
        return events

    def interrupt(self, at: datetime, reason: str) -> list[EquipmentEvent]:
        events = [self._event("INTERRUPTED", track, at, reason) for track in self.tracks.values() if track.confirmed]
        self.tracks.clear()
        return events
