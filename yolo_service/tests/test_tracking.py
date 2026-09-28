import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import unittest
import sys
from uuid import uuid4
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from yolo_tracker.detector import ClassMapping, MappingError, YOLODetector
from yolo_tracker.camera_manager import CameraManager, CameraRuntime, CameraWorker, LatestFrameCapture, _tracker_capture_url
from yolo_tracker.outbox import EventOutbox
from yolo_tracker.tracking import CameraSpec, Detection, EquipmentEvent, VisitState


class TrackingTests(unittest.TestCase):
    def test_box_is_percent_of_original_frame_and_clamped(self) -> None:
        box = Detection(4, "excavator", 0.9, (-20, 90, 740, 510)).percent_box(720, 480)
        self.assertEqual(box, {"x": 0.0, "y": 18.75, "w": 100.0, "h": 81.25})

    def test_nan_box_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Detection(1, "crane", 0.8, (0, 0, float("nan"), 2)).percent_box(10, 10)

    def test_mapping_requires_exact_checkpoint_taxonomy(self) -> None:
        mapping = ClassMapping.load(Path("class_mapping.yolo26m-multiscale-v1.json"))
        mapping.verify({
            0: "asphalt_paver", 1: "backhoe_loader", 2: "bulldozer",
            3: "concrete_mixer", 4: "concrete_mixer_truck", 5: "concrete_pump_truck",
            6: "concrete_vibrator", 7: "crane", 8: "dump_truck", 9: "excavator",
            10: "forklift", 11: "hydraulic_cropper", 12: "jack_hammer", 13: "loader",
            14: "mobile_crane", 15: "motor_grader", 16: "pile_driver", 17: "road_roller",
            18: "skid_steer_loader", 19: "tanker_truck", 20: "telehandler", 21: "tower_crane",
            22: "tractor", 23: "trailer", 24: "truck", 25: "truck_mounted_crane",
            26: "wheel_loader",
        })
        with self.assertRaises(MappingError):
            mapping.verify({0: "asphalt_paver"})

    def test_unsupported_and_unknown_classes_are_never_equipment(self) -> None:
        mapping = ClassMapping.load(Path("class_mapping.yolo26m-multiscale-v1.json"))
        self.assertIsNone(mapping.equipment_type(0))
        self.assertIsNone(mapping.equipment_type(5))
        self.assertIsNone(mapping.equipment_type(99))
        self.assertEqual(mapping.equipment_type(9), "excavator")
        self.assertEqual(mapping.equipment_type(25), "manipulator")

    def test_confirmation_vote_grace_and_interruption(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=UTC)
        state = VisitState("cam-1", uuid4(), 2, 5, "yolo26m-multiscale-v1")
        detection = Detection(9, "truck", 0.6, (0, 0, 1, 1))
        self.assertEqual(state.observe([detection], at, 30), [])
        events = state.observe([Detection(9, "dump_truck", 0.1, (0, 0, 1, 1))], at + timedelta(seconds=1), 30)
        self.assertEqual([event.event_type for event in events], ["FIRST_SEEN"])
        self.assertEqual(events[0].equipment_type, "truck")
        self.assertEqual(events[0].track_id.split(":")[0], f"s-{state.session_id}")
        self.assertEqual(state.expire(at + timedelta(seconds=6)), [])
        events = state.expire(at + timedelta(seconds=7))
        self.assertEqual([event.event_type for event in events], ["LAST_SEEN"])
        self.assertEqual(events[0].observed_at, at + timedelta(seconds=1))
        state.observe([detection], at + timedelta(seconds=8), 30)
        state.observe([detection], at + timedelta(seconds=9), 30)
        events = state.interrupt(at + timedelta(seconds=10), "capture_read_failed")
        self.assertEqual([event.event_type for event in events], ["INTERRUPTED"])
        self.assertEqual(events[0].last_seen_at, at + timedelta(seconds=9))
        self.assertEqual(events[0].confidence, 0.6)

    def test_events_use_last_confidence_for_voted_type(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=UTC)
        state = VisitState("cam-1", uuid4(), 3, 5, "yolo26m-multiscale-v1")
        state.observe([Detection(9, "truck", 0.7, (0, 0, 1, 1))], at, 30)
        state.observe([Detection(9, "dump_truck", 0.3, (0, 0, 1, 1))], at + timedelta(seconds=1), 30)
        events = state.observe([Detection(9, "truck", 0.2, (0, 0, 1, 1))], at + timedelta(seconds=2), 30)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].equipment_type, "truck")
        self.assertEqual(events[0].confidence, 0.2)
        self.assertEqual(events[0].payload()["confidence"], 0.2)

    def test_worker_expires_long_gap_before_reusing_raw_track_id(self) -> None:
        class Detector:
            mapping = SimpleNamespace(version="test-model")

            async def track(self, _camera_id, _frame):
                return [Detection(5, "excavator", 0.8, (0, 0, 64, 48))], 1.0

        class Hub:
            def publish(self, _message: dict) -> None:
                pass

        async def check() -> list[EquipmentEvent]:
            at = datetime(2026, 1, 1, tzinfo=UTC)
            emitted: list[EquipmentEvent] = []

            async def emit(event: EquipmentEvent) -> None:
                emitted.append(event)

            worker = CameraWorker(
                CameraSpec("cam-1", "site-1", "work", "rtsp://unused"),
                SimpleNamespace(confirm_frames=1, lost_grace_seconds=5, event_heartbeat_seconds=30, snapshot_interval_seconds=60),
                Detector(),
                Hub(),
                object(),
                emit,
            )
            worker._last_snapshot_at = at
            frame = SimpleNamespace(shape=(480, 640, 3))
            await worker._process(frame, at)
            await worker._process(frame, at + timedelta(seconds=20))
            return emitted

        events = asyncio.run(check())
        self.assertEqual([event.event_type for event in events], ["FIRST_SEEN", "LAST_SEEN", "FIRST_SEEN"])
        self.assertEqual(events[1].last_seen_at, events[0].first_seen_at)
        self.assertEqual(events[2].first_seen_at, events[2].observed_at)

    def test_camera_discovery_accepts_backend_camel_case_contract(self) -> None:
        spec = CameraManager._camera_spec({"id": "cam-1", "siteId": "site-1", "zoneKind": "work", "rtspUrl": "rtsp://example/stream"})
        self.assertEqual((spec.id, spec.site_id, spec.zone_kind), ("cam-1", "site-1", "work"))

    def test_same_type_tracks_survive_grace_without_duplicate_visits(self) -> None:
        at = datetime(2026, 1, 1, tzinfo=UTC)
        session_a = VisitState("cam-1", uuid4(), 2, 5, "yolo26m-multiscale-v1")
        vehicles = [
            Detection(41, "excavator", 0.9, (10, 10, 110, 110)),
            Detection(42, "excavator", 0.8, (210, 10, 310, 110)),
        ]

        self.assertEqual(session_a.observe(vehicles, at, 60), [])
        first_seen = session_a.observe(vehicles, at + timedelta(seconds=1), 60)
        self.assertEqual([event.event_type for event in first_seen], ["FIRST_SEEN", "FIRST_SEEN"])
        first_ids = {event.track_id for event in first_seen}
        self.assertEqual(len(first_ids), 2)

        # Two missing frames remain inside grace and do not close or recreate visits.
        self.assertEqual(session_a.observe([], at + timedelta(seconds=2), 60), [])
        self.assertEqual(session_a.expire(at + timedelta(seconds=2)), [])
        self.assertEqual(session_a.observe([], at + timedelta(seconds=3), 60), [])
        self.assertEqual(session_a.expire(at + timedelta(seconds=3)), [])
        returned = session_a.observe(vehicles, at + timedelta(seconds=4), 60)
        self.assertFalse(any(event.event_type == "FIRST_SEEN" for event in returned))
        self.assertEqual({track.public_id for track in session_a.tracks.values()}, first_ids)

        session_b = VisitState("cam-1", uuid4(), 1, 5, "yolo26m-multiscale-v1")
        new_session_first_seen = session_b.observe(vehicles, at + timedelta(seconds=5), 60)
        self.assertEqual(len({event.track_id for event in new_session_first_seen}), 2)
        self.assertTrue(first_ids.isdisjoint({event.track_id for event in new_session_first_seen}))

    def test_tracker_rtsp_credentials_are_encoded_without_replacing_source(self) -> None:
        self.assertEqual(
            _tracker_capture_url("rtsp://video:8554/cam-c1?transport=tcp#part", "key:/? @%"),
            "rtsp://sk-tracker:key%3A%2F%3F%20%40%25@video:8554/cam-c1?transport=tcp#part",
        )
        self.assertEqual(
            _tracker_capture_url("rtsp://other:existing@video:8554/cam-c1", "new-key"),
            "rtsp://other:existing@video:8554/cam-c1",
        )
        self.assertEqual(_tracker_capture_url("http://video/cam-c1", "new-key"), "http://video/cam-c1")

    def test_frame_message_uses_current_detection_confidence(self) -> None:
        class Detector:
            mapping = SimpleNamespace(version="test-model")

            async def track(self, _camera_id, _frame):
                return [Detection(5, "excavator", 0.2, (0, 0, 64, 48))], 1.0

        class Hub:
            messages: list[dict] = []

            def publish(self, message: dict) -> None:
                self.messages.append(message)

        async def check() -> dict:
            at = datetime(2026, 1, 1, tzinfo=UTC)
            worker = CameraWorker(
                CameraSpec("cam-1", "site-1", "work", "rtsp://unused"),
                SimpleNamespace(confirm_frames=1, lost_grace_seconds=5, event_heartbeat_seconds=30, snapshot_interval_seconds=60),
                Detector(),
                hub := Hub(),
                object(),
                lambda _event: asyncio.sleep(0),
            )
            worker._visit.observe([Detection(5, "excavator", 0.95, (0, 0, 64, 48))], at - timedelta(seconds=1), 30)
            worker._last_snapshot_at = at
            await worker._process(SimpleNamespace(shape=(480, 640, 3)), at)
            return hub.messages[-1]

        self.assertEqual(asyncio.run(check())["objects"][0]["confidence"], 0.2)


    def test_detector_keeps_separate_tracker_objects_when_cameras_interleave(self) -> None:
        class Model:
            def __init__(self) -> None:
                self.predictor = SimpleNamespace()
                self.created = 0

            def track(self, **_kwargs):
                if not hasattr(self.predictor, "trackers"):
                    self.created += 1
                    self.predictor.trackers = [object()]
                return [SimpleNamespace(boxes=None)]

        detector = YOLODetector(Path("unused.pt"), ClassMapping("test", {}, {}), "cpu", 0.1, 320, "bytetrack")
        detector.model = Model()
        detector._track_sync("camera-a", object())
        tracker_a = detector._trackers["camera-a"]
        detector._track_sync("camera-b", object())
        tracker_b = detector._trackers["camera-b"]
        detector._track_sync("camera-a", object())
        self.assertIs(detector._trackers["camera-a"], tracker_a)
        self.assertIsNot(tracker_a, tracker_b)
        self.assertEqual(detector.model.created, 2)

    def test_capture_never_falls_back_to_unauthenticated_opencv_open(self) -> None:
        calls: list[tuple] = []

        class Capture:
            def isOpened(self) -> bool:
                return False

            def release(self) -> None:
                pass

        class CV2:
            CAP_FFMPEG = 1900
            CAP_PROP_OPEN_TIMEOUT_MSEC = 53
            CAP_PROP_READ_TIMEOUT_MSEC = 54

            class error(Exception):
                pass

            @staticmethod
            def VideoCapture(*args):
                calls.append(args)
                return Capture()

        capture = LatestFrameCapture("rtsp://video:8554/cam-c1", 3)
        with patch.dict(sys.modules, {"cv2": CV2}):
            capture._run()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "rtsp://video:8554/cam-c1")
        self.assertEqual(capture.error, "capture_open_failed")
    def test_outbox_replays_a_duplicate_event_once(self) -> None:
        async def check() -> list[dict]:
            with TemporaryDirectory() as temp:
                outbox = EventOutbox(Path(temp) / "outbox.sqlite3", 10)
                event = EquipmentEvent(uuid4(), "FIRST_SEEN", "cam-1", uuid4(), "s-session:1", "crane", datetime(2026, 1, 1, tzinfo=UTC))
                await outbox.put(event)
                await outbox.put(event)
                delivered: list[dict] = []
                stop = asyncio.Event()

                async def send(payload: dict) -> bool:
                    delivered.append(payload)
                    stop.set()
                    return True

                await outbox.drain(send, stop)
                outbox.close()
                return delivered

        delivered = asyncio.run(check())
        self.assertEqual(len(delivered), 1)
        self.assertNotIn("confidence", delivered[0])


class ControlTests(unittest.TestCase):
    @staticmethod
    def _settings(max_cameras: int = 2) -> SimpleNamespace:
        return SimpleNamespace(
            api_key="test-key",
            max_cameras=max_cameras,
            connect_timeout_seconds=1,
            confirm_frames=1,
            lost_grace_seconds=1,
            event_heartbeat_seconds=1,
            target_fps=5,
            snapshot_interval_seconds=30,
            reconnect_max_seconds=1,
        )

    @staticmethod
    def _worker_type() -> type:
        class FakeWorker:
            created = 0

            def __init__(self, spec, *_):
                type(self).created += 1
                self.spec = spec
                self.runtime = CameraRuntime(spec)
                self._visit = SimpleNamespace(tracks={})

            def update_metadata(self, spec):
                self.spec = spec
                self.runtime.spec = spec

            def start(self):
                return None

            async def stop(self, reason):
                self.runtime.status = "stopped"
                self.runtime.last_interruption_reason = reason

        return FakeWorker

    def _manager(self, payload) -> tuple[CameraManager, httpx.AsyncClient]:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/tracker/cameras")
            return httpx.Response(200, json=payload())

        client = httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(handler))
        return CameraManager(self._settings(), SimpleNamespace(), SimpleNamespace(), client, lambda event: None), client

    def test_authorizes_camera_commands(self) -> None:
        from fastapi.testclient import TestClient
        from yolo_tracker.config import Settings

        class Manager:
            async def command(self, operation, camera_id, request_id=None):
                return {"camera_id": camera_id, "operation": operation, "accepted": True, "status": "starting", "already_running": False}

            def status(self, camera_id=None):
                return []

        async def start(service):
            service.manager = Manager()
            service.model_ready = True
            service.discovery_ready = True

        async def close(_service):
            return None

        with patch.dict(os.environ, {"SK_TRACKER_API_KEY": "control-secret"}, clear=False):
            from yolo_tracker.app import Service, create_app

            app = create_app(Settings.from_env())
            with patch.object(Service, "start", start), patch.object(Service, "close", close), TestClient(app) as client:
                self.assertEqual(client.post("/cameras/start", json={"camera_id": "cam-1"}).status_code, 401)
                response = client.post("/cameras/start", headers={"X-Api-Key": "control-secret"}, json={"camera_id": "cam-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "starting")

    def test_start_is_idempotent_and_metadata_update_keeps_worker(self) -> None:
        cameras = [{"id": "cam-1", "siteId": "site-1", "zoneKind": "work", "rtspUrl": "rtsp://video/one"}]
        manager, client = self._manager(lambda: cameras)
        FakeWorker = self._worker_type()

        async def check():
            with patch("yolo_tracker.camera_manager.CameraWorker", FakeWorker):
                first = await manager.command("start", "cam-1")
                second = await manager.command("start", "cam-1")
                worker = manager.workers["cam-1"]
                cameras[0]["zoneKind"] = "storage"
                await manager.reconcile()
                metadata_preserved = worker is manager.workers["cam-1"]
                cameras[0]["rtspUrl"] = "rtsp://video/two"
                await manager.reconcile()
                return first, second, metadata_preserved, worker is not manager.workers["cam-1"], manager.workers["cam-1"].spec.zone_kind, FakeWorker.created

        try:
            first, second, preserved, restarted, zone, created = asyncio.run(check())
        finally:
            asyncio.run(client.aclose())
        self.assertTrue(first["accepted"])
        self.assertTrue(second["already_running"])
        self.assertTrue(preserved)
        self.assertTrue(restarted)
        self.assertEqual(zone, "storage")
        self.assertEqual(created, 2)

    def test_stop_unknown_worker_is_idempotent(self) -> None:
        manager, client = self._manager(lambda: [])

        async def check():
            return await manager.command("stop", "deleted-camera")

        try:
            response = asyncio.run(check())
        finally:
            asyncio.run(client.aclose())
        self.assertEqual(response, {
            "camera_id": "deleted-camera",
            "operation": "stop",
            "accepted": True,
            "status": "stopped",
            "already_running": False,
        })

    def test_request_id_replays_restart_without_second_reset(self) -> None:
        cameras = [{"id": "cam-1", "siteId": "site-1", "zoneKind": "work", "rtspUrl": "rtsp://video/one"}]
        manager, client = self._manager(lambda: cameras)
        FakeWorker = self._worker_type()

        async def check():
            with patch("yolo_tracker.camera_manager.CameraWorker", FakeWorker):
                await manager.command("start", "cam-1")
                first = await manager.command("restart", "cam-1", "retry-1")
                replay = await manager.command("restart", "cam-1", "retry-1")
                return first, replay, FakeWorker.created

        try:
            first, replay, created = asyncio.run(check())
        finally:
            asyncio.run(client.aclose())
        self.assertEqual(first, replay)
        self.assertEqual(created, 2)

    def test_stale_reconcile_does_not_recreate_after_stop(self) -> None:
        manager, client = self._manager(lambda: [])
        spec = CameraSpec("cam-1", "site-1", "work", "rtsp://video/one")
        FakeWorker = self._worker_type()

        async def check():
            started, release = asyncio.Event(), asyncio.Event()
            calls = 0

            async def fetch():
                nonlocal calls
                calls += 1
                if calls == 1:
                    started.set()
                    await release.wait()
                    return {"cam-1": spec}
                return {}

            with patch("yolo_tracker.camera_manager.CameraWorker", FakeWorker):
                await manager._ensure(spec)
                with patch.object(manager, "_fetch_wanted", fetch):
                    reconciliation = asyncio.create_task(manager.reconcile())
                    await started.wait()
                    await manager.command("stop", "cam-1")
                    release.set()
                    await reconciliation
                return len(manager.workers)

        try:
            worker_count = asyncio.run(check())
        finally:
            asyncio.run(client.aclose())
        self.assertEqual(worker_count, 0)

    def test_stopped_manager_ignores_inflight_reconcile(self) -> None:
        manager, client = self._manager(lambda: [])
        spec = CameraSpec("cam-1", "site-1", "work", "rtsp://video/one")
        FakeWorker = self._worker_type()

        async def check():
            started, release = asyncio.Event(), asyncio.Event()

            async def fetch():
                started.set()
                await release.wait()
                return {"cam-1": spec}

            with patch("yolo_tracker.camera_manager.CameraWorker", FakeWorker), patch.object(manager, "_fetch_wanted", fetch):
                reconciliation = asyncio.create_task(manager.reconcile())
                await started.wait()
                await manager.stop()
                release.set()
                await reconciliation
                return len(manager.workers)

        try:
            worker_count = asyncio.run(check())
        finally:
            asyncio.run(client.aclose())
        self.assertEqual(worker_count, 0)

    def test_service_close_cancels_discovery_before_stopping_manager(self) -> None:
        async def check():
            with patch.dict(os.environ, {"SK_TRACKER_API_KEY": "close-secret"}, clear=False):
                from yolo_tracker.app import Service

                events: list[str] = []

                class Manager:
                    async def stop(self):
                        events.append("manager_stop")

                async def discovery():
                    try:
                        await asyncio.Event().wait()
                    finally:
                        events.append("discovery_cancelled")

                service = object.__new__(Service)
                service.stop = asyncio.Event()
                service.manager = Manager()
                service.discovery_task = asyncio.create_task(discovery())
                service.delivery_task = None
                service.client = None
                service.outbox = SimpleNamespace(close=lambda: events.append("outbox_closed"))
                await asyncio.sleep(0)
                await service.close()
                return events

        events = asyncio.run(check())
        self.assertEqual(events, ["discovery_cancelled", "manager_stop", "outbox_closed"])

    def test_concurrent_commands_keep_one_current_worker(self) -> None:
        cameras = [{"id": "cam-1", "siteId": "site-1", "zoneKind": "work", "rtspUrl": "rtsp://video/one"}]
        manager, client = self._manager(lambda: cameras)
        FakeWorker = self._worker_type()

        async def check():
            with patch("yolo_tracker.camera_manager.CameraWorker", FakeWorker):
                await asyncio.gather(manager.command("start", "cam-1"), manager.command("restart", "cam-1"))
                return len(manager.workers), manager.status("cam-1")[0]["status"]

        try:
            worker_count, status = asyncio.run(check())
        finally:
            asyncio.run(client.aclose())
        self.assertEqual(worker_count, 1)
        self.assertEqual(status, "starting")

    def test_reconcile_marks_capacity_and_keeps_workers_on_discovery_failure(self) -> None:
        cameras = [
            {"id": "cam-1", "siteId": "site-1", "zoneKind": "work", "rtspUrl": "rtsp://video/one"},
            {"id": "cam-2", "siteId": "site-1", "zoneKind": "work", "rtspUrl": "rtsp://video/two"},
        ]
        manager, client = self._manager(lambda: cameras)
        manager.settings = self._settings(max_cameras=1)
        FakeWorker = self._worker_type()
        async def check():
            with patch("yolo_tracker.camera_manager.CameraWorker", FakeWorker):
                await manager.reconcile()
                capacity = await manager.command("start", "cam-2")
                cameras.clear()
                with patch.object(manager, "_fetch_wanted", side_effect=httpx.ConnectError("backend unavailable")):
                    with self.assertRaises(httpx.ConnectError):
                        await manager.reconcile()
                return capacity, len(manager.workers), {item["camera_id"]: item["status"] for item in manager.status()}

        try:
            capacity, worker_count, statuses = asyncio.run(check())
        finally:
            asyncio.run(client.aclose())
        self.assertFalse(capacity["accepted"])
        self.assertEqual(capacity["status"], "waiting_capacity")
        self.assertEqual(worker_count, 1)
        self.assertEqual(statuses, {"cam-1": "starting", "cam-2": "waiting_capacity"})


if __name__ == "__main__":
    unittest.main()
