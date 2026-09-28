"""Служебные команды боевого запуска: первый администратор в пустой базе, забытый пароль, базовые правила."""

import asyncio
import io

import httpx
import pytest
from sqlalchemy import func, select

from app import manage, seed
from app.db import SessionLocal, engine
from app.main import app
from app.models import AuditEvent, Rule, Site, User


def _run(coro):  # noqa: ANN001, ANN202
    async def wrapped():  # noqa: ANN202
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(wrapped())


def _command(monkeypatch, *argv: str, password: str | None = None) -> None:  # noqa: ANN001
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{password}\n" if password is not None else ""))
    manage.main(list(argv))


async def _login(login: str, password: str) -> int:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8100") as http:
        return (await http.post("/api/auth/login", json={"login": login, "password": password})).status_code


async def _state() -> dict:
    async with SessionLocal() as session:
        return {
            "users": list(await session.scalars(select(User))),
            "sites": await session.scalar(select(func.count()).select_from(Site)),
            "rules": await session.scalar(select(func.count()).select_from(Rule)),
            "audit": list(await session.scalars(select(AuditEvent).order_by(AuditEvent.id))),
        }


def test_first_admin_in_empty_database(monkeypatch):
    _run(seed._drop_everything())
    _command(monkeypatch, "create-admin", "--login", " Chief ", "--name", "Орлов  Павел", "--password-stdin", password="secret-1")

    state = _run(_state())
    [admin] = state["users"]
    assert (admin.login, admin.name, admin.role) == ("chief", "Орлов Павел", "admin")
    assert state["sites"] == 0 and state["rules"] == 9  # без демо-данных, но с базовой методикой
    assert state["audit"][-1].actor_login == "командная строка" and state["audit"][-1].action == "user.create"
    assert _run(_login("chief", "secret-1")) == 200

    with pytest.raises(SystemExit, match="уже есть"):
        _command(monkeypatch, "create-admin", "--login", "chief", "--name", "Двойник", password="secret-2")
    with pytest.raises(SystemExit, match="от 6 до 200"):
        _command(monkeypatch, "create-admin", "--login", "second", "--name", "Второй", password="123")
    with pytest.raises(SystemExit, match="Логин"):
        _command(monkeypatch, "create-admin", "--login", "главный", "--name", "Главный", password="secret-2")

    # забыл пароль — новый задают командой на сервере, старый больше не подходит
    _command(monkeypatch, "set-password", "--login", "chief", password="new-secret")
    assert _run(_login("chief", "new-secret")) == 200
    assert _run(_login("chief", "secret-1")) == 401
    with pytest.raises(SystemExit, match="нет"):
        _command(monkeypatch, "set-password", "--login", "nobody", password="new-secret")


def test_methodology_command_restores_only_missing_rules(monkeypatch):
    _run(seed._drop_everything())
    _run(seed.prepare_database(demo_data=False))  # в демо-плане заняты все базовые правила — берём базу без демо-данных

    async def edit() -> None:
        async with SessionLocal() as session:
            await session.delete(await session.get(Rule, "landscaping"))
            (await session.get(Rule, "asphalt")).stage_name = "Асфальт (своя редакция)"
            await session.commit()

    _run(edit())
    _command(monkeypatch, "methodology")

    async def check() -> tuple[str, str]:
        async with SessionLocal() as session:
            return (await session.get(Rule, "landscaping")).stage_name, (await session.get(Rule, "asphalt")).stage_name

    assert _run(check()) == ("Благоустройство", "Асфальт (своя редакция)")  # удалённое вернулось, изменённое не тронуто


def test_keycloak_mode_refuses_local_admin(monkeypatch):
    monkeypatch.setattr(manage.get_settings(), "keycloak_issuer", "http://localhost:8080/realms/stroykontrol")
    with pytest.raises(SystemExit, match="Keycloak"):
        _command(monkeypatch, "create-admin", "--login", "chief", "--name", "Орлов Павел", password="secret-1")
