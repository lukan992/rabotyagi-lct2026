from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
from time import monotonic
from queue import Empty, Full, Queue
import threading
from urllib.parse import quote, urlsplit, urlunsplit
from typing import Any, Awaitable, Callable
from uuid import uuid4

import httpx

from .config import Settings
from .detector import YOLODetector
from .tracking import CameraSpec, Detection, EquipmentEvent, VisitState, iso, utc_now
from .ws_stream import StreamHub

log = logging.getLogger(__name__)

def _tracker_capture_url(url: str, api_key: str) -> str:
    """Apply the documented tracker credential only to credential-less backend RTSP URLs."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "rtsp" or parsed.username is not None:
        return url
    host = parsed.hostname
    if host is None:
        raise ValueError("RTSP camera URL has no host")
    host_part = f"[{host}]" if ":" in host else host
    port_part = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"sk-tracker:{quote(api_key, safe='')}@{host_part}{port_part}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


@dataclass(slots=True)
class CameraRuntime:
    spec: CameraSpec
    session_id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "starting"
    last_frame_at: datetime | None = None
    last_detection_at: datetime | None = None
    last_processed_at: datetime | None = None
    last_error: str | None = None
    last_interruption_reason: str | None = None
    fps: float = 0.0
    inference_ms: float = 0.0
    drop_count: int = 0
    reconnect_count: int = 0


class LatestFrameCapture:
    """A dedicated decoder keeps one newest frame, so inference never consumes RTSP backlog."""

    def __init__(self, url: str, timeout_seconds: float):
        self.url, self.timeout_seconds = url, timeout_seconds
        self.frames: Queue[tuple[datetime, Any]] = Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="rtsp-capture", daemon=True)
        self.error: str | None = None
        self.opened = False
        self.drop_count = 0
        self._cap: Any | None = None

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        import cv2

        milliseconds = int(self.timeout_seconds * 1000)
        try:
            cap = cv2.VideoCapture(
                self.url,
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, milliseconds, cv2.CAP_PROP_READ_TIMEOUT_MSEC, milliseconds],
            )
        except cv2.error:
            self.error = "capture_open_failed:ffmpeg_backend_unavailable"
            return
        self._cap = cap
        try:
            if not cap.isOpened():
                self.error = "capture_open_failed"
                return
            self.opened = True
            while not self.stop.is_set():
                ok, frame = cap.read()
                received_at = utc_now()
                if not ok or frame is None:
                    self.error = "capture_read_failed"
                    return
                if self.frames.full():
                    try:
                        self.frames.get_nowait()
                        self.drop_count += 1
                    except Empty:
                        pass
                try:
                    self.frames.put_nowait((received_at, frame))
                except Full:
                    self.drop_count += 1
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            cap.release()

    def get(self, timeout: float) -> tuple[datetime, Any] | None:
        try:
            return self.frames.get(timeout=timeout)
        except Empty:
            return None

    def request_stop(self) -> None:
        self.stop.set()

    def join(self, timeout: float) -> bool:
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()

class CameraWorker:
    def __init__(self, spec: CameraSpec, settings: Settings, detector: YOLODetector, hub: StreamHub, client: httpx.AsyncClient, emit: Callable[[EquipmentEvent], Awaitable[None]]):
        self.spec, self.settings, self.detector, self.hub, self.client, self.emit = spec, settings, detector, hub, client, emit
        self.runtime = CameraRuntime(spec)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._visit = self._new_visit()
        self._last_snapshot_at: datetime | None = None
        self._snapshot_retry_at: datetime | None = None
        self._snapshot_attempts = 0
        self._capture: LatestFrameCapture | None = None

    def _new_visit(self) -> VisitState:
        session = uuid4()
        self.runtime.session_id = str(session)
        return VisitState(self.spec.id, session, self.settings.confirm_frames, self.settings.lost_grace_seconds, self.detector.mapping.version)

    def update_metadata(self, spec: CameraSpec) -> None:
        """Keep the tracker session when backend changes non-connection metadata."""
        self.spec = spec
        self.runtime.spec = spec

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name=f"camera:{self.spec.id}")

    async def _close_capture(self, capture: LatestFrameCapture) -> None:
        capture.request_stop()
        try:
            joined = await asyncio.wait_for(asyncio.to_thread(capture.join, 2.0), timeout=2.2)
        except TimeoutError:
            joined = False
        if not joined:
            log.warning("capture thread outlived bounded shutdown", extra={"camera_id": self.spec.id})

    async def stop(self, reason: str) -> None:
        self.runtime.status = "stopping"
        self._stop.set()
        capture = self._capture
        if capture is not None:
            capture.request_stop()
        for event in self._visit.interrupt(utc_now(), reason):
            await self.emit(event)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # A task cancelled before entering run() has no finally block to join its decoder.
        if self._capture is capture and capture is not None:
            await self._close_capture(capture)
            self._capture = None
        self.detector.drop_camera(self.spec.id)
        self.runtime.status = "stopped"
        self.runtime.last_interruption_reason = reason
    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            capture = LatestFrameCapture(self.spec.rtsp_url, self.settings.connect_timeout_seconds)
            self._capture = capture
            capture.start()
            self.runtime.status, self.runtime.last_error = ("reconnecting" if self.runtime.reconnect_count else "starting"), None
            try:
                while not self._stop.is_set():
                    item = await asyncio.to_thread(capture.get, self.settings.connect_timeout_seconds + 1)
                    self.runtime.drop_count += capture.drop_count
                    capture.drop_count = 0
                    if item is None:
                        raise RuntimeError(capture.error or "capture_frame_timeout")
                    received_at, frame = item
                    started = monotonic()
                    await self._process(frame, received_at)
                    self.runtime.status, self.runtime.last_error, backoff = "running", None, 1.0
                    delay = 1 / self.settings.target_fps - (monotonic() - started)
                    if delay > 0:
                        await self._wait(delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._offline(str(exc))
                await self._wait(backoff)
                backoff = min(self.settings.reconnect_max_seconds, backoff * 2)
            finally:
                await self._close_capture(capture)
                self._capture = None

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _offline(self, reason: str) -> None:
        if self.runtime.status != "reconnecting":
            for event in self._visit.interrupt(utc_now(), "camera_offline"):
                await self.emit(event)
            self._visit = self._new_visit()
            self.runtime.reconnect_count += 1
        self.runtime.status, self.runtime.last_error = "reconnecting", reason
        self.runtime.last_interruption_reason = "camera_offline"
        log.warning("camera unavailable", extra={"camera_id": self.spec.id, "reason": reason})

    async def _process(self, frame: Any, at: datetime) -> None:
        h, w = frame.shape[:2]
        detections, elapsed = await self.detector.track(self.spec.id, frame)
        previous_at = self.runtime.last_processed_at
        self.runtime.last_frame_at, self.runtime.inference_ms, self.runtime.last_processed_at = at, elapsed, at
        self.runtime.fps = 0.0 if previous_at is None else 1 / max((at - previous_at).total_seconds(), 0.001)
        if detections:
            self.runtime.last_detection_at = at
        events = self._visit.expire(at)
        events.extend(self._visit.observe(detections, at, self.settings.event_heartbeat_seconds))
        for event in events:
            await self.emit(event)
        objects: list[dict[str, Any]] = []
        snapshot_objects: list[dict[str, Any]] = []
        for detection in detections:
            track = self._visit.tracks.get(detection.tracker_id)
            if track is None:
                continue
            try:
                box = detection.percent_box(w, h)
            except ValueError:
                log.warning("invalid detection box excluded", extra={"camera_id": self.spec.id, "track_id": detection.tracker_id})
                continue
            confidence = round(detection.confidence, 5)
            snapshot_object = {"type": track.voted_type(), "confidence": confidence, "box": box}
            snapshot_objects.append(snapshot_object)
            objects.append({"track_id": track.public_id, **snapshot_object})
        self.hub.publish({"camera_id": self.spec.id, "ts": iso(at), "frame_w": w, "frame_h": h, "objects": objects})
        if (
            (self._last_snapshot_at is None or (at - self._last_snapshot_at).total_seconds() >= self.settings.snapshot_interval_seconds)
            and (self._snapshot_retry_at is None or at >= self._snapshot_retry_at)
        ):
            await self._snapshot(frame, at, snapshot_objects)

    async def _snapshot(self, frame: Any, at: datetime, objects: list[dict[str, Any]]) -> None:
        if self._last_snapshot_at is not None and at <= self._last_snapshot_at:
            return
        import cv2

        ok, encoded = await asyncio.to_thread(cv2.imencode, ".jpg", frame)
        if not ok:
            log.warning("JPEG encoding failed", extra={"camera_id": self.spec.id})
            return
        try:
            response = await self.client.post(
                "/api/ingest/snapshots", headers={"X-Api-Key": self.settings.api_key},
                data={"camera_id": self.spec.id, "taken_at": iso(at), "model": self.detector.mapping.version, "detections": json.dumps(objects)},
                files={"image": (f"{self.spec.id}.jpg", encoded.tobytes(), "image/jpeg")},
            )
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            if response.status_code != 202 or body.get("accepted") is not True:
                raise RuntimeError(f"snapshot rejected: {response.status_code}")
            self._last_snapshot_at, self._snapshot_retry_at, self._snapshot_attempts = at, None, 0
        except Exception as exc:
            self._snapshot_attempts += 1
            if self._snapshot_attempts >= 5:
                # Do not retry one unavailable backend indefinitely; a later fresh frame starts the next interval.
                self._last_snapshot_at, self._snapshot_retry_at, self._snapshot_attempts = at, None, 0
            else:
                self._snapshot_retry_at = at + timedelta(seconds=min(60, 2 ** self._snapshot_attempts))
            log.warning("snapshot delivery failed", extra={"camera_id": self.spec.id, "error": type(exc).__name__})


class CameraManager:
    """Owns one asynchronous worker per backend-authorized camera.

    Every mutation goes through the camera's lock, shared by HTTP commands and
    reconciliation.  The backend list is the desired-state source; failed
    discovery never becomes an empty desired set.
    """

    def __init__(self, settings: Settings, detector: YOLODetector, hub: StreamHub, client: httpx.AsyncClient, emit: Callable[[EquipmentEvent], Awaitable[None]]):
        self.settings, self.detector, self.hub, self.client, self.emit = settings, detector, hub, client, emit
        self.workers: dict[str, CameraWorker] = {}
        self._states: dict[str, CameraRuntime] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._command_generation = 0
        self._request_results: dict[str, OrderedDict[str, dict[str, Any]]] = {}
        self._stop = asyncio.Event()
    def _lock(self, camera_id: str) -> asyncio.Lock:
        return self._locks.setdefault(camera_id, asyncio.Lock())

    @staticmethod
    def _camera_spec(item: dict[str, Any], api_key: str = "") -> CameraSpec:
        """Backend's shared API uses camelCase; accept snake_case for contract-compatible deployments."""
        camera_id = item["id"]
        site_id = item.get("site_id") or item.get("siteId")
        zone_kind = item.get("zone_kind") or item.get("zoneKind")
        rtsp_url = item.get("rtsp_url") or item.get("rtspUrl")
        if not all((site_id, zone_kind, rtsp_url)):
            raise ValueError(f"camera {camera_id!r} has incomplete tracker stream data")
        return CameraSpec(
            str(camera_id), str(site_id), str(zone_kind), _tracker_capture_url(str(rtsp_url), api_key),
            item.get("demo_clip") or item.get("demoClip"),
        )

    async def _fetch_wanted(self) -> dict[str, CameraSpec]:
        response = await self.client.get("/api/tracker/cameras", headers={"X-Api-Key": self.settings.api_key})
        response.raise_for_status()
        cameras = response.json()
        if not isinstance(cameras, list):
            raise ValueError("camera discovery response is not a list")
        wanted: dict[str, CameraSpec] = {}
        for item in cameras:
            if not isinstance(item, dict):
                raise ValueError("camera discovery contains a non-object entry")
            spec = self._camera_spec(item, self.settings.api_key)
            if spec.id in wanted:
                raise ValueError(f"camera discovery contains duplicate id {spec.id!r}")
            wanted[spec.id] = spec
        return wanted

    async def _retire(self, camera_id: str, reason: str) -> CameraRuntime | None:
        worker = self.workers.pop(camera_id, None)
        if worker is None:
            runtime = self._states.get(camera_id)
            if runtime is not None:
                runtime.status = "stopped"
                runtime.last_interruption_reason = reason
            return runtime
        await worker.stop(reason)
        self._states[camera_id] = worker.runtime
        return worker.runtime

    async def _ensure(self, spec: CameraSpec, *, restart: bool = False) -> tuple[CameraRuntime, bool]:
        if self._stop.is_set():
            runtime = self._states.get(spec.id) or CameraRuntime(spec=spec, session_id="", status="stopped")
            runtime.spec, runtime.status = spec, "stopped"
            self._states[spec.id] = runtime
            return runtime, False
        worker = self.workers.get(spec.id)
        if worker is not None and (restart or worker.spec.rtsp_url != spec.rtsp_url):
            await self._retire(spec.id, "restart" if restart else "rtsp_url_changed")
            worker = None
        if worker is not None:
            worker.update_metadata(spec)
            self._states[spec.id] = worker.runtime
            return worker.runtime, True
        if len(self.workers) >= self.settings.max_cameras:
            runtime = self._states.get(spec.id)
            if runtime is None or runtime.status != "waiting_capacity":
                runtime = CameraRuntime(spec=spec, session_id="", status="waiting_capacity")
                self._states[spec.id] = runtime
            else:
                runtime.spec = spec
            runtime.last_error = f"maximum active camera capacity ({self.settings.max_cameras}) reached"
            return runtime, False
        worker = CameraWorker(spec, self.settings, self.detector, self.hub, self.client, self.emit)
        self.workers[spec.id] = worker
        self._states[spec.id] = worker.runtime
        worker.start()
        return worker.runtime, False

    async def _converge_camera(self, camera_id: str, spec: CameraSpec | None, reason: str) -> CameraRuntime | None:
        if spec is None:
            return await self._retire(camera_id, reason)
        runtime, _ = await self._ensure(spec)
        return runtime

    async def command(self, operation: str, camera_id: str, request_id: str | None = None) -> dict[str, Any]:
        """Apply one control command without doing capture or inference in the request."""
        if operation not in {"start", "stop", "restart"}:
            raise ValueError(f"unsupported camera operation {operation!r}")
        async with self._lock(camera_id):
            if request_id:
                cached = self._request_results.get(camera_id, {}).get(request_id)
                if cached is not None:
                    return dict(cached)
            self._command_generation += 1

            def finish(result: dict[str, Any]) -> dict[str, Any]:
                if request_id and result["accepted"]:
                    results = self._request_results.setdefault(camera_id, OrderedDict())
                    results[request_id] = dict(result)
                    results.move_to_end(request_id)
                    while len(results) > 64:
                        results.popitem(last=False)
                return result

            if operation == "stop":
                runtime = await self._retire(camera_id, "worker_stopped")
                # STOP is immediately effective even when a deleted camera can
                # no longer be discovered. A successful follow-up lookup only
                # reverses a stale STOP when the database still wants it on.
                try:
                    current = (await self._fetch_wanted()).get(camera_id)
                except Exception:
                    current = None
                if current is not None:
                    runtime, _ = await self._ensure(current)
                status = runtime.status if runtime else "stopped"
                return finish(self._command_response(camera_id, operation, True, status, False))
            try:
                wanted = await self._fetch_wanted()
            except Exception as exc:
                log.warning("camera control discovery failed", extra={"camera_id": camera_id, "operation": operation, "error": type(exc).__name__})
                runtime = self._states.get(camera_id)
                return finish(self._command_response(camera_id, operation, False, runtime.status if runtime else "error", False))

            spec = wanted.get(camera_id)
            if spec is None:
                await self._retire(camera_id, "camera_removed")
                return finish(self._command_response(camera_id, operation, False, "stopped", False))

            runtime, already_running = await self._ensure(spec, restart=operation == "restart")
            accepted = runtime.status != "waiting_capacity"
            # A command can arrive after a DB update; a second authoritative
            # lookup makes the resulting worker converge rather than preserve
            # an older request's source URL or enabled state.
            try:
                current = (await self._fetch_wanted()).get(camera_id)
            except Exception as exc:
                log.warning("post-command camera discovery failed", extra={"camera_id": camera_id, "operation": operation, "error": type(exc).__name__})
                return finish(self._command_response(camera_id, operation, accepted, runtime.status, already_running))
            runtime = await self._converge_camera(camera_id, current, "camera_removed")
            if current is None:
                return finish(self._command_response(camera_id, operation, False, "stopped", False))
            assert runtime is not None
            return finish(self._command_response(camera_id, operation, runtime.status != "waiting_capacity", runtime.status, already_running))

    @staticmethod
    def _command_response(camera_id: str, operation: str, accepted: bool, status: str, already_running: bool) -> dict[str, Any]:
        return {
            "camera_id": camera_id,
            "operation": operation,
            "accepted": accepted,
            "status": status,
            "already_running": already_running,
        }

    async def reconcile(self) -> None:
        # Capture before the network wait. A command that completes while the
        # backend returns an older list invalidates this whole snapshot.
        generation = self._command_generation
        wanted = await self._fetch_wanted()
        if self._stop.is_set() or self._command_generation != generation:
            return
        known_ids = set(self.workers) | set(self._states)
        for camera_id in known_ids - set(wanted):
            async with self._lock(camera_id):
                if self._stop.is_set() or self._command_generation != generation:
                    return
                await self._retire(camera_id, "camera_removed")
        for camera_id, spec in wanted.items():
            async with self._lock(camera_id):
                if self._stop.is_set() or self._command_generation != generation:
                    return
                await self._ensure(spec)

    async def stop(self) -> None:
        self._stop.set()
        for camera_id in list(self.workers):
            async with self._lock(camera_id):
                await self._retire(camera_id, "service_shutdown")

    def status(self, camera_id: str | None = None) -> list[dict[str, Any]]:
        now = utc_now()
        records: list[dict[str, Any]] = []
        for current_id, runtime in self._states.items():
            if camera_id is not None and current_id != camera_id:
                continue
            worker = self.workers.get(current_id)
            active_tracks = 0
            if worker is not None:
                active_tracks = sum(track.confirmed for track in worker._visit.tracks.values())
            frame_age = (now - runtime.last_frame_at).total_seconds() if runtime.last_frame_at else None
            records.append({
                "camera_id": runtime.spec.id,
                "site_id": runtime.spec.site_id,
                "zone_kind": runtime.spec.zone_kind,
                "tracker_session_id": runtime.session_id or None,
                "session_id": runtime.session_id or None,
                "status": runtime.status,
                "last_frame_at": iso(runtime.last_frame_at) if runtime.last_frame_at else None,
                "last_detection_at": iso(runtime.last_detection_at) if runtime.last_detection_at else None,
                "frame_age_seconds": round(frame_age, 3) if frame_age is not None else None,
                "processed_fps": round(runtime.fps, 2),
                "fps": round(runtime.fps, 2),
                "inference_latency_ms": round(runtime.inference_ms, 2),
                "inference_ms": round(runtime.inference_ms, 2),
                "active_confirmed_tracks": active_tracks,
                "drop_count": runtime.drop_count,
                "reconnect_count": runtime.reconnect_count,
                "last_error": runtime.last_error,
                "last_interruption_reason": runtime.last_interruption_reason,
            })
        return sorted(records, key=lambda item: item["camera_id"])
