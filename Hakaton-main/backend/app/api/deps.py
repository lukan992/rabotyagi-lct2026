"""Общие помощники для обработчиков API."""

from typing import Annotated

from fastapi import HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Camera, Site, User
from app.security import ensure_site_access, visible_site_ids


async def get_site(session: AsyncSession, user: User, site_id: str) -> Site:
    ensure_site_access(user, site_id)
    site = await session.get(Site, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект не найден")
    return site


async def get_camera(session: AsyncSession, user: User, camera_id: str) -> Camera:
    camera = await session.get(Camera, camera_id)
    if camera is None or camera.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Камера не найдена")
    ensure_site_access(user, camera.site_id)
    return camera


def scope(query, column, user: User, site_id: str | None = None):
    """Ограничить выборку объектами, которые видит пользователь, и (если задан) одним объектом."""
    allowed = visible_site_ids(user)
    if allowed is not None:
        query = query.where(column.in_(allowed))
    if site_id:
        query = query.where(column == site_id)
    return query


# Параметры запроса — в camelCase, как и весь JSON
SiteIdQuery = Annotated[str | None, Query(alias="siteId", description="Ограничить одним объектом")]
CameraIdQuery = Annotated[str | None, Query(alias="cameraId")]

__all__ = ["CameraIdQuery", "SiteIdQuery", "get_camera", "get_site", "scope", "select"]
