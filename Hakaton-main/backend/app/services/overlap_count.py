"""Approximate cross-camera equipment count from stored overlap geometry.

This is deliberately separate from rule verdicts: the model proposes common
visual regions, while epipolar matching of box footpoints is only an estimate.
"""

import math
from collections import Counter

from app.models import Camera, CameraOverlap, Detection, Snapshot
from app.services.camera_overlaps import viewpoint_fingerprint

MAX_EPIPOLAR_DISTANCE_PX = 25.0


def _inside_ring(point: tuple[float, float], ring: list) -> bool:
    x, y = point
    inside = False
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        x0, y0 = previous
        x1, y1 = current
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def _inside_regions(point: tuple[float, float], regions: list) -> bool:
    return any(
        _inside_ring(point, region["exterior"])
        and not any(_inside_ring(point, hole) for hole in region.get("holes", []))
        for region in regions
    )


def _footpoint(detection: Detection, width: int, height: int) -> tuple[float, float]:
    return ((detection.x + detection.w / 2) * width / 100, (detection.y + detection.h) * height / 100)


def _epipolar_distance(f: list, point0: tuple[float, float], point1: tuple[float, float]) -> float:
    x0, y0 = point0
    x1, y1 = point1
    a = f[0][0] * x0 + f[0][1] * y0 + f[0][2]
    b = f[1][0] * x0 + f[1][1] * y0 + f[1][2]
    c = f[2][0] * x0 + f[2][1] * y0 + f[2][2]
    residual = abs(a * x1 + b * y1 + c)
    norm1 = math.hypot(a, b)
    norm0 = math.hypot(f[0][0] * x1 + f[1][0] * y1 + f[2][0],
                       f[0][1] * x1 + f[1][1] * y1 + f[2][1])
    return max(residual / norm0, residual / norm1) if norm0 > 1e-9 and norm1 > 1e-9 else math.inf


def _valid_f(matrix: object) -> bool:
    return (
        isinstance(matrix, list) and len(matrix) == 3
        and all(isinstance(row, list) and len(row) == 3 for row in matrix)
        and all(isinstance(value, (int, float)) and math.isfinite(value) for row in matrix for value in row)
    )


def estimate_counts(
    latest: dict[str, Snapshot], cameras: list[Camera], overlaps: list[CameraOverlap], *,
    max_skew_seconds: float, work_only: bool = True,
) -> tuple[dict[str, int] | None, int]:
    """Return estimated unique counts and number of cross-camera matches.

    No usable measured pair means there is no estimate, rather than a fake
    copy of the raw camera sum. Every accepted edge must be same class, inside
    both proposed regions, nearly simultaneous and epipolar-consistent.
    """
    work = {
        camera.id: camera for camera in cameras
        if camera.id in latest and (not work_only or camera.zone.kind == "work")
    }
    nodes: list[tuple[str, Detection]] = [
        (camera_id, detection)
        for camera_id in sorted(work)
        for detection in latest[camera_id].detections
    ]
    index_by_camera: dict[str, list[int]] = {}
    for index, (camera_id, _) in enumerate(nodes):
        index_by_camera.setdefault(camera_id, []).append(index)
    parents = list(range(len(nodes)))
    group_cameras = [{camera_id} for camera_id, _ in nodes]

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    candidates: list[tuple[float, int, int]] = []
    usable_pairs = 0
    for overlap in overlaps:
        camera0, camera1 = work.get(overlap.camera0_id), work.get(overlap.camera1_id)
        if camera0 is None or camera1 is None or overlap.status != "candidate_overlap" or not overlap.result:
            continue
        if (overlap.camera0_fingerprint != viewpoint_fingerprint(camera0)
                or overlap.camera1_fingerprint != viewpoint_fingerprint(camera1)):
            continue
        snapshot0, snapshot1 = latest[camera0.id], latest[camera1.id]
        if abs((snapshot0.taken_at - snapshot1.taken_at).total_seconds()) > max_skew_seconds:
            continue
        images = overlap.result.get("images", [])
        geometry = overlap.result.get("geometry", {})
        f = geometry.get("F_original_pixels") if isinstance(geometry, dict) else None
        if len(images) != 2 or not _valid_f(f):
            continue
        if any(not isinstance(image.get("regions"), list) for image in images):
            continue
        usable_pairs += 1
        for index0 in index_by_camera.get(camera0.id, []):
            detection0 = nodes[index0][1]
            point0 = _footpoint(detection0, images[0]["width"], images[0]["height"])
            if not _inside_regions(point0, images[0]["regions"]):
                continue
            for index1 in index_by_camera.get(camera1.id, []):
                detection1 = nodes[index1][1]
                if detection0.equipment_type != detection1.equipment_type:
                    continue
                point1 = _footpoint(detection1, images[1]["width"], images[1]["height"])
                if not _inside_regions(point1, images[1]["regions"]):
                    continue
                distance = _epipolar_distance(f, point0, point1)
                if distance <= MAX_EPIPOLAR_DISTANCE_PX:
                    candidates.append((distance, index0, index1))
    if not usable_pairs:
        return None, 0
    matches = 0
    for _, index0, index1 in sorted(candidates):
        root0, root1 = root(index0), root(index1)
        if root0 == root1 or group_cameras[root0] & group_cameras[root1]:
            continue
        parents[root1] = root0
        group_cameras[root0].update(group_cameras[root1])
        matches += 1
    counts = Counter(nodes[index][1].equipment_type for index in range(len(nodes)) if root(index) == index)
    return dict(counts), matches
