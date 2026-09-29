"""Matrix permissions and multi-camera counting from measured geometry."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.db import SessionLocal, utcnow
from app.models import Camera, CameraOverlap
from app.services.camera_overlaps import viewpoint_fingerprint
from app.services.overlap_count import estimate_counts
from tests.conftest import login_as


def _camera(camera_id: str, zone_kind: str = "work"):
    return SimpleNamespace(
        id=camera_id, scheme="rtsp", host=f"{camera_id}.example", port=554, path="/stream",
        zone=SimpleNamespace(kind=zone_kind),
    )


def _snapshot(camera_id: str, equipment_type: str = "excavator", y: float = 40):
    detection = SimpleNamespace(equipment_type=equipment_type, x=40.0, y=y, w=20.0, h=10.0)
    return SimpleNamespace(camera_id=camera_id, taken_at=datetime(2026, 9, 29, 10, tzinfo=UTC), detections=[detection])


def _overlap(camera0, camera1):  # noqa: ANN001
    full = {"exterior": [[0, 0], [1279, 0], [1279, 719], [0, 719]], "holes": []}
    # y0 == y1 is the epipolar relation for this synthetic geometry.
    f = [[0, 0, 0], [0, 0, -1], [0, 1, 0]]
    return SimpleNamespace(
        camera0_id=camera0.id, camera1_id=camera1.id, status="candidate_overlap",
        camera0_fingerprint=viewpoint_fingerprint(camera0), camera1_fingerprint=viewpoint_fingerprint(camera1),
        result={"images": [{"width": 1280, "height": 720, "regions": [full]}] * 2,
                "geometry": {"F_original_pixels": f}},
    )


def test_three_cameras_can_resolve_to_one_observation() -> None:
    cameras = [_camera("a"), _camera("b"), _camera("c")]
    snapshots = {camera.id: _snapshot(camera.id) for camera in cameras}
    count, matched = estimate_counts(
        snapshots, cameras, [_overlap(cameras[0], cameras[1]), _overlap(cameras[1], cameras[2])],
        max_skew_seconds=60,
    )
    assert count == {"excavator": 1}
    assert matched == 2


def test_no_geometry_does_not_pretend_to_estimate() -> None:
    cameras = [_camera("a"), _camera("b")]
    snapshots = {camera.id: _snapshot(camera.id) for camera in cameras}
    count, matched = estimate_counts(snapshots, cameras, [], max_skew_seconds=60)
    assert count is None and matched == 0
    snapshots["b"] = _snapshot("b", y=80)
    count, matched = estimate_counts(snapshots, cameras, [_overlap(*cameras)], max_skew_seconds=60)
    assert count == {"excavator": 2} and matched == 0


def test_site_total_includes_non_work_camera_without_changing_work_count() -> None:
    cameras = [_camera("a"), _camera("b", "entrance")]
    snapshots = {camera.id: _snapshot(camera.id) for camera in cameras}
    overlap = _overlap(*cameras)
    work_count, _ = estimate_counts(snapshots, cameras, [overlap], max_skew_seconds=60)
    site_count, matched = estimate_counts(
        snapshots, cameras, [overlap], max_skew_seconds=60, work_only=False,
    )
    assert work_count is None
    assert site_count == {"excavator": 1} and matched == 1


@pytest.mark.anyio
async def test_matrix_lists_pending_camera_pairs_for_one_site(client) -> None:
    headers = await login_as(client, "manager")
    response = await client.get("/api/sites/s1/camera-overlaps", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["siteId"] == "s1"
    assert [camera["id"] for camera in body["cameras"]] == ["c1", "c2", "c3"]
    assert body["matrix"][0] == ["self", "pending", "pending"]
    assert body["pairs"] == []


@pytest.mark.anyio
async def test_matrix_reads_persisted_result_and_marks_changed_stream_stale(client) -> None:
    headers = await login_as(client, "manager")
    async with SessionLocal() as session:
        camera0, camera1 = await session.get(Camera, "c1"), await session.get(Camera, "c2")
        session.add(CameraOverlap(
            camera0_id="c1", camera1_id="c2", site_id="s1", status="candidate_overlap",
            result={"images": [{"width": 1280, "height": 720, "regions": []}] * 2, "geometry": {}},
            snapshot0_id="s-a", snapshot1_id="s-b",
            camera0_fingerprint=viewpoint_fingerprint(camera0),
            camera1_fingerprint=viewpoint_fingerprint(camera1),
            attempts=1, analyzed_at=utcnow(),
        ))
        await session.commit()
    response = await client.get("/api/sites/s1/camera-overlaps", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["matrix"][0][1] == "candidate_overlap"
    assert response.json()["pairs"][0]["snapshot0Id"] == "s-a"

    async with SessionLocal() as session:
        camera = await session.get(Camera, "c1")
        camera.path = "/moved"
        await session.commit()
    response = await client.get("/api/sites/s1/camera-overlaps", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["matrix"][0][1] == "stale"
