from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.config import get_settings
from app.models import User
from app.schemas import DemoLoginIn, LoginIn, TokenOut, UserOut, user_out
from app.security import CurrentUser, Session, check_password, create_token
from app.services import audit

router = APIRouter(prefix="/auth", tags=["Вход"])
settings = get_settings()


@router.post("/login", response_model=TokenOut, summary="Вход по логину и паролю")
async def login(body: LoginIn, session: Session, request: Request) -> TokenOut:
    login_name = body.login.strip().lower()
    user = await session.scalar(select(User).where(User.login == login_name))
    password_ok = await check_password(body.password, user.password_hash if user else None)
    if user is None or not user.is_active or not password_ok:
        audit.record(
            session,
            request,
            None,
            "login.failed",
            f"Неудачная попытка входа под логином «{login_name[:64]}»",
            actor_login=login_name[:64],
        )
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    audit.record(
        session, request, user, "login", "Вошёл в систему по паролю", entity_type="user", entity_id=user.id, entity_name=user.name
    )
    await session.commit()
    return TokenOut(token=create_token(user), user=user_out(user))


@router.post("/demo-login", response_model=TokenOut, summary="Быстрый вход по роли (только демо-режим)")
async def demo_login(body: DemoLoginIn, session: Session, request: Request) -> TokenOut:
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Быстрый вход выключен")
    user = await session.scalar(select(User).where(User.role == body.role, User.is_active).order_by(User.id).limit(1))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нет пользователя с такой ролью")
    audit.record(
        session,
        request,
        user,
        "login.demo",
        "Вошёл быстрым входом (демо-режим)",
        entity_type="user",
        entity_id=user.id,
        entity_name=user.name,
    )
    await session.commit()
    return TokenOut(token=create_token(user), user=user_out(user))


@router.get("/me", response_model=UserOut, summary="Кто я")
async def me(user: CurrentUser) -> UserOut:
    return user_out(user)
