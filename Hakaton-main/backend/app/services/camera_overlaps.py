"""One-time overlap measurements for fixed pairs of site cameras."""

import asyncio
import hashlib
import json
import logging
from datetime import timedelta
from itertools import combinations
from pathlib import Path

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import Camera, CameraOverlap, Site, Snapshot

log = logging.getLogger("stroykontrol.camera_overlaps")
settings = get_settings()
_pair_locks: dict[tuple[str, str], asyncio.Lock] = {}


class OverlapServiceError(Exception):
    pass


def viewpoint_fingerprint(camera: Camera) -> str:
    """Changes to the configured stream invalidate a measurement; physical movement needs manual recompute."""
    source = [camera.id, camera.scheme, camera.host, camera.port, camera.path]
    return hashlib.sha256(json.dumps(source, ensure_ascii=False).encode()).hexdigest()


def frame_path(snapshot: Snapshot) -> Path:
    prefix = f"/media/frames/{snapshot.camera_id}/"
    if not snapshot.image_url.startswith(prefix):
        raise ValueError("Snapshot is not a saved camera frame")
    name = snapshot.image_url.removeprefix(prefix)
    if not name.endswith(".jpg") or name != Path(name).name:
        raise ValueError("Invalid saved frame name")
    return settings.frames_dir / snapshot.camera_id / name


async def _candidates(session, camera_id: str) -> list[Snapshot]:  # noqa: ANN001
    rows = list(
        await session.scalars(
            select(Snapshot)
            .where(Snapshot.camera_id == camera_id, Snapshot.image_url.like(f"/media/frames/{camera_id}/%"))
            .order_by(Snapshot.taken_at.desc())
            .limit(12)
        )
    )
    return [row for row in rows if frame_path(row).is_file()]


def _choose_pair(left: list[Snapshot], right: list[Snapshot]) -> tuple[Snapshot, Snapshot] | None:
    if not left or not right:
        return None
    # Prefer frames from the same minute, then the newest such pair. Static viewpoints need no exact sync.
    return min(
        ((a, b) for a in left for b in right),
        key=lambda pair: (abs((pair[0].taken_at - pair[1].taken_at).total_seconds()),
                          -min(pair[0].taken_at, pair[1].taken_at).timestamp()),
    )


def _validate_result(payload: dict) -> None:
    if payload.get("schema_version") != "camera-overlap-v1" or payload.get("status") not in {
        "candidate_overlap", "insufficient_evidence"
    }:
        raise OverlapServiceError("Unexpected overlap service result")
    images = payload.get("images")
    if not isinstance(images, list) or len(images) != 2 or any(
        not isinstance(image, dict) or not isinstance(image.get("regions"), list)
        or not isinstance(image.get("width"), int) or not isinstance(image.get("height"), int)
        for image in images
    ):
        raise OverlapServiceError("Overlap result has invalid image geometry")
    if not isinstance(payload.get("geometry"), dict):
        raise OverlapServiceError("Overlap result lacks geometry")


async def _request_overlap(image0: bytes, image1: bytes) -> dict:
    if not settings.overlap_service_url:
        raise OverlapServiceError("Overlap service URL is not configured")
    headers = {"X-Overlap-Token": settings.overlap_service_token} if settings.overlap_service_token else {}
    try:
        async with httpx.AsyncClient(timeout=settings.overlap_timeout_seconds) as client:
            response = await client.post(
                settings.overlap_service_url.rstrip("/") + "/analyze",
                headers=headers,
                files={"image0": ("camera0.jpg", image0, "image/jpeg"),
                       "image1": ("camera1.jpg", image1, "image/jpeg")},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise OverlapServiceError(f"Overlap service request failed: {type(error).__name__}") from error
    if not isinstance(payload, dict):
        raise OverlapServiceError("Overlap service returned non-object JSON")
    _validate_result(payload)
    return payload


async def analyze_pair(site_id: str, camera0_id: str, camera1_id: str, *, force: bool = False) -> CameraOverlap | None:
    """Send two stored JPEGs to the real model and persist its measured pair result."""
    if camera0_id >= camera1_id:
        raise ValueError("Camera IDs must be sorted and distinct")
    key = camera0_id, camera1_id
    async with _pair_locks.setdefault(key, asyncio.Lock()):
        async with SessionLocal() as session:
            camera0, camera1 = await session.get(Camera, camera0_id), await session.get(Camera, camera1_id)
            if any(c is None or c.site_id != site_id or c.deleted_at is not None or not c.enabled
                   for c in (camera0, camera1)):
                return None
            fp0, fp1 = viewpoint_fingerprint(camera0), viewpoint_fingerprint(camera1)
            existing = await session.get(CameraOverlap, key)
            unchanged = bool(existing and existing.camera0_fingerprint == fp0 and existing.camera1_fingerprint == fp1)
            if unchanged and not force:
                if existing.status == "candidate_overlap" or (existing.status == "insufficient_evidence" and existing.attempts >= 3):
                    return existing
                if existing.status == "service_error" and utcnow() - existing.analyzed_at < timedelta(minutes=5):
                    return existing
            chosen = _choose_pair(await _candidates(session, camera0_id), await _candidates(session, camera1_id))
            if chosen is None:
                return None if force else existing
            snapshot0, snapshot1 = chosen
            if unchanged and not force and existing.status == "insufficient_evidence" and (
                existing.snapshot0_id, existing.snapshot1_id
            ) == (snapshot0.id, snapshot1.id):
                return existing
            path0, path1 = frame_path(snapshot0), frame_path(snapshot1)
            ids = snapshot0.id, snapshot1.id
        try:
            image0, image1 = await asyncio.gather(asyncio.to_thread(path0.read_bytes), asyncio.to_thread(path1.read_bytes))
            payload, error = await _request_overlap(image0, image1), None
            status = payload["status"]
        except (OSError, OverlapServiceError) as exc:
            payload, error, status = None, str(exc)[:300], "service_error"
            log.warning("Overlap analysis failed for %s/%s: %s", camera0_id, camera1_id, error)
        async with SessionLocal() as session:
            row = await session.get(CameraOverlap, key)
            if row is None:
                row = CameraOverlap(camera0_id=camera0_id, camera1_id=camera1_id, site_id=site_id)
                session.add(row)
            previous_status = row.status
            row.camera0_fingerprint, row.camera1_fingerprint = fp0, fp1
            row.snapshot0_id, row.snapshot1_id = ids
            row.status, row.result, row.error = status, payload, error
            row.attempts = (
                row.attempts + 1
                if status == "insufficient_evidence" and unchanged and not force
                and previous_status == "insufficient_evidence" else 1
            )
            row.analyzed_at = utcnow()
            await session.commit()
            return row


async def sync_once() -> None:
    """Calibrate missing or stale pairs without blocking the site's minute check."""
    if not settings.overlap_service_url:
        return
    async with SessionLocal() as session:
        cameras = list(await session.scalars(select(Camera).where(Camera.enabled, Camera.deleted_at.is_(None))))
        sites = set(await session.scalars(select(Site.id)))
    by_site: dict[str, list[str]] = {}
    for camera in cameras:
        if camera.site_id in sites:
            by_site.setdefault(camera.site_id, []).append(camera.id)
    for site_id, camera_ids in by_site.items():
        for camera0_id, camera1_id in combinations(sorted(camera_ids), 2):
            await analyze_pair(site_id, camera0_id, camera1_id)


async def sync_loop() -> None:
    while True:
        try:
            await sync_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Camera overlap synchronization failed")
        await asyncio.sleep(settings.overlap_sync_interval_seconds)
