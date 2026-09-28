"""Журнал действий (администратор): кто, когда и что сделал — добавил или удалил камеру, сменил пароль, закрыл отклонение."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from app.models import AuditEvent
from app.schemas import AuditOut, audit_out
from app.security import Session, require_roles

router = APIRouter(prefix="/audit", tags=["Журнал действий"], dependencies=[require_roles("admin")])


@router.get("", response_model=list[AuditOut], summary="Журнал действий, новые сверху")
async def list_events(
    session: Session,
    actor: Annotated[str | None, Query(description="Логин того, кто действовал")] = None,
    action: Annotated[str | None, Query(description="Вид действия или его начало: camera, camera.delete, login…")] = None,
    entity_type: Annotated[str | None, Query(alias="entityType")] = None,
    q: Annotated[str | None, Query(description="Поиск по тексту записи")] = None,
    since: datetime | None = None,
    before_id: Annotated[int | None, Query(alias="beforeId", description="Постранично: записи старше этой")] = None,
    limit: int = 100,
) -> list[AuditOut]:
    query = select(AuditEvent)
    if actor:
        query = query.where(AuditEvent.actor_login == actor)
    if action:
        query = query.where(or_(AuditEvent.action == action, AuditEvent.action.startswith(f"{action}.")))
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(AuditEvent.summary.ilike(pattern), AuditEvent.actor_name.ilike(pattern), AuditEvent.entity_name.ilike(pattern))
        )
    if since:
        query = query.where(AuditEvent.at >= since)
    if before_id:
        query = query.where(AuditEvent.id < before_id)
    rows = await session.scalars(query.order_by(AuditEvent.id.desc()).limit(min(max(limit, 1), 500)))
    return [audit_out(e) for e in rows]
