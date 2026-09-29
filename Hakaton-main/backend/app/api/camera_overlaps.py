"""Pairwise overlap matrix and explicit recomputation for fixed site cameras."""

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select

from app.api.deps import get_site
from app.config import get_settings
from app.models import Camera, CameraOverlap
from app.schemas import ApiModel
from app.security import SITE_MANAGERS, CurrentUser, Session, require_roles
from app.services.camera_overlaps import analyze_pair, viewpoint_fingerprint

router = APIRouter(prefix="/sites/{site_id}/camera-overlaps", tags=["Пересечения камер"])
managers = [require_roles(*SITE_MANAGERS)]


class RecomputePair(ApiModel):
    camera0_id: str = Field(min_length=1)
    camera1_id: str = Field(min_length=1)


def _pair_out(row: CameraOverlap, cameras: dict[str, Camera]) -> dict:
    result = row.result or {}
    images = result.get("images") or [{}, {}]
    return {
        "camera0Id": row.camera0_id,
        "camera1Id": row.camera1_id,
        "status": row.status,
        "stale": (row.camera0_fingerprint != viewpoint_fingerprint(cameras[row.camera0_id])
                  or row.camera1_fingerprint != viewpoint_fingerprint(cameras[row.camera1_id])),
        "regions0": images[0].get("regions", []),
        "regions1": images[1].get("regions", []),
        "image0Size": [images[0].get("width"), images[0].get("height")],
        "image1Size": [images[1].get("width"), images[1].get("height")],
        "snapshot0Id": row.snapshot0_id,
        "snapshot1Id": row.snapshot1_id,
        "modelVersion": result.get("module_version"),
        "matches": (result.get("geometry") or {}).get("matches"),
        "inliers": (result.get("geometry") or {}).get("selected_inliers"),
        "rejectionReasons": (result.get("geometry") or {}).get("rejection_reasons", []),
        "analyzedAt": row.analyzed_at.isoformat(),
        "error": row.error,
    }


@router.get("")
async def matrix(site_id: str, user: CurrentUser, session: Session) -> dict:
    await get_site(session, user, site_id)
    camera_rows = list(
        await session.scalars(
            select(Camera).where(Camera.site_id == site_id, Camera.enabled, Camera.deleted_at.is_(None))
            .order_by(Camera.position, Camera.id)
        )
    )
    cameras = {camera.id: camera for camera in camera_rows}
    rows = list(await session.scalars(select(CameraOverlap).where(CameraOverlap.site_id == site_id)))
    pairs = [row for row in rows if row.camera0_id in cameras and row.camera1_id in cameras]
    by_pair = {(row.camera0_id, row.camera1_id): row for row in pairs}
    ids = list(cameras)
    statuses = []
    for a in ids:
        cells = []
        for b in ids:
            row = by_pair.get(tuple(sorted((a, b)))) if a != b else None
            cells.append("self" if a == b else "pending" if row is None else
                         "stale" if _pair_out(row, cameras)["stale"] else row.status)
        statuses.append(cells)
    return {
        "siteId": site_id,
        "cameras": [{"id": camera.id, "name": camera.name} for camera in camera_rows],
        "matrix": statuses,
        "pairs": [_pair_out(row, cameras) for row in pairs],
    }


@router.post("/recompute", dependencies=managers)
async def recompute(site_id: str, body: RecomputePair, user: CurrentUser, session: Session) -> dict:
    await get_site(session, user, site_id)
    if not get_settings().overlap_service_url:
        raise HTTPException(503, "Сервис поиска пересечений не настроен")
    if body.camera0_id == body.camera1_id:
        raise HTTPException(422, "Нужны две разные камеры")
    camera0_id, camera1_id = sorted((body.camera0_id, body.camera1_id))
    row = await analyze_pair(site_id, camera0_id, camera1_id, force=True)
    if row is None:
        raise HTTPException(404, "Камеры объекта или сохранённые кадры не найдены")
    if row.status == "service_error":
        raise HTTPException(503, row.error or "Сервис поиска пересечений недоступен")
    cameras = {camera.id: camera for camera in (await session.get(Camera, camera0_id), await session.get(Camera, camera1_id))}
    return _pair_out(row, cameras)
