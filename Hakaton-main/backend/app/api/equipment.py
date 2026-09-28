"""Read-only equipment visit journal for authorized site users."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import get_site
from app.models import Camera, EquipmentEvent, EquipmentVisit
from app.schemas import (
    EquipmentEventsPageOut,
    EquipmentEventOut,
    EquipmentVisitsPageOut,
    EquipmentVisitOut,
    EquipmentType,
    equipment_event_out,
    equipment_visit_out,
)
from app.security import CurrentUser, Session

router = APIRouter(tags=["Журнал наблюдения техники"])


def _range_valid(from_at: datetime | None, to_at: datetime | None) -> None:
    for value in (from_at, to_at):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Временные фильтры должны содержать часовой пояс")
    if from_at is not None and to_at is not None and from_at > to_at:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "from не может быть позже to")


async def _camera_for_site(session: Session, site_id: str, camera_id: str | None) -> None:
    if camera_id is None:
        return
    camera = await session.get(Camera, camera_id)
    if camera is None or camera.site_id != site_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Камера не принадлежит объекту")


@router.get(
    "/sites/{site_id}/equipment-events",
    response_model=EquipmentEventsPageOut,
    summary="Границы появления и исчезновения техники по камерам объекта",
)
async def equipment_events(
    site_id: str,
    user: CurrentUser,
    session: Session,
    camera_id: Annotated[str | None, Query(alias="cameraId")] = None,
    equipment_class: Annotated[EquipmentType | None, Query(alias="equipmentClass")] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EquipmentEventsPageOut:
    await get_site(session, user, site_id)
    _range_valid(from_at, to_at)
    await _camera_for_site(session, site_id, camera_id)
    query = select(EquipmentEvent).where(EquipmentEvent.site_id == site_id)
    if camera_id:
        query = query.where(EquipmentEvent.camera_id == camera_id)
    if equipment_class:
        query = query.where(EquipmentEvent.equipment_class == equipment_class)
    if from_at:
        query = query.where(EquipmentEvent.timestamp >= from_at)
    if to_at:
        query = query.where(EquipmentEvent.timestamp <= to_at)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        await session.scalars(query.order_by(EquipmentEvent.timestamp.desc(), EquipmentEvent.event_id.desc()).limit(limit).offset(offset))
    )
    return EquipmentEventsPageOut(
        items=[equipment_event_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/sites/{site_id}/equipment-visits",
    response_model=EquipmentVisitsPageOut,
    summary="Сглаженные интервалы наблюдения техники по камерам объекта",
)
async def equipment_visits(
    site_id: str,
    user: CurrentUser,
    session: Session,
    camera_id: Annotated[str | None, Query(alias="cameraId")] = None,
    equipment_class: Annotated[EquipmentType | None, Query(alias="equipmentClass")] = None,
    visit_status: Annotated[Literal["active", "completed", "lost"] | None, Query(alias="status")] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EquipmentVisitsPageOut:
    await get_site(session, user, site_id)
    _range_valid(from_at, to_at)
    await _camera_for_site(session, site_id, camera_id)
    query = select(EquipmentVisit).where(EquipmentVisit.site_id == site_id)
    if camera_id:
        query = query.where(EquipmentVisit.camera_id == camera_id)
    if equipment_class:
        query = query.where(EquipmentVisit.equipment_class == equipment_class)
    if visit_status:
        query = query.where(EquipmentVisit.status == visit_status)
    if from_at:
        query = query.where(EquipmentVisit.last_seen_at >= from_at)
    if to_at:
        query = query.where(EquipmentVisit.first_seen_at <= to_at)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        await session.scalars(query.order_by(EquipmentVisit.last_seen_at.desc(), EquipmentVisit.id.desc()).limit(limit).offset(offset))
    )
    return EquipmentVisitsPageOut(
        items=[equipment_visit_out(row) for row in rows], total=total, limit=limit, offset=offset
    )
