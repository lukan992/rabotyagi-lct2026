"""Журнал действий: кто, когда и что сделал. Запись добавляется в ту же транзакцию, что и само действие."""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, User

ROLE_TITLES = {"foreman": "прораб", "manager": "руководитель проекта", "inspector": "инспектор", "admin": "администратор"}


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()  # за nginx настоящий адрес — здесь
    return forwarded or (request.client.host if request.client else "")


def record(
    session: AsyncSession,
    request: Request | None,
    actor: User | None,
    action: str,
    summary: str,
    *,
    entity_type: str = "",
    entity_id: str | None = None,
    entity_name: str = "",
    details: dict | None = None,
    actor_login: str = "",
) -> None:
    """Записать действие. actor=None — неизвестный (например, неудачный вход: тогда actor_login — что ввели)."""
    session.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            actor_login=actor.login if actor else actor_login,
            actor_name=actor.name if actor else "",
            actor_role=actor.role if actor else "",
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name[:300],
            summary=summary,
            details=details or {},
            ip=client_ip(request)[:64],
        )
    )


def changes(before: dict, after: dict) -> dict:
    """Что поменялось: {"поле": [было, стало]} — только отличающиеся поля."""
    return {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
