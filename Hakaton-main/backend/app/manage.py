"""Служебные команды для боевого запуска — без демо-данных и без входа по роли.

    uv run python -m app.manage create-admin --login admin --name "Орлов Павел"   # первый администратор
    uv run python -m app.manage set-password --login admin                         # забытый пароль
    uv run python -m app.manage methodology                                        # вернуть базовые правила

Пароль спрашивается дважды и не виден при вводе; в скрипте его можно передать строкой на вход: --password-stdin.
В Docker: docker compose exec backend /app/.venv/bin/python -m app.manage create-admin --login admin --name "…"
Команды доводят базу до последней миграции; в новую базу ставят базовую методику «этап → техника», демо-данных не добавляют.
"""

import argparse
import asyncio
import getpass
import re
import sys

from sqlalchemy import func, select

from app import dbschema, seed
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import User, new_id
from app.security import hash_password
from app.services import audit, methodology

CLI = "командная строка"  # кто сделал — в журнале действий
LOGIN = re.compile(r"^[A-Za-z0-9._-]{2,64}$")  # как у сотрудника, заведённого в интерфейсе


def _password(from_stdin: bool) -> str:
    if from_stdin or not sys.stdin.isatty():
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Пароль (не короче 6 символов): ")
        if password != getpass.getpass("Повторите пароль: "):
            raise SystemExit("Пароли не совпали — ничего не изменено")
    if not 6 <= len(password) <= 200:
        raise SystemExit("Пароль — от 6 до 200 символов")
    return password


async def _ready() -> None:
    """База по последней миграции. Базу версии без миграций не трогаем: её переводит сервер (демо-базу — пересоздаёт)."""
    async with engine.connect() as conn:
        if await conn.run_sync(dbschema.created_before_migrations):
            raise SystemExit("База создана версией без миграций — сначала запустите сервер, он её переведёт")
    await seed.prepare_database(demo_data=False)


async def create_admin(login: str, name: str, phone: str, password: str) -> None:
    login, name = login.strip().lower(), " ".join(name.split())
    if not LOGIN.match(login):
        raise SystemExit("Логин — латинские буквы, цифры, точка, дефис, подчёркивание; от 2 до 64 символов")
    if len(name) < 2:
        raise SystemExit("Укажите фамилию и имя")
    await _ready()
    async with SessionLocal() as session:
        if await session.scalar(select(User.id).where(func.lower(User.login) == login)):
            raise SystemExit(f"Сотрудник с логином {login} уже есть. Новый пароль ему: set-password --login {login}")
        user = User(
            id=new_id("u"), login=login, name=name, role="admin", phone=phone.strip(), password_hash=hash_password(password)
        )
        session.add(user)
        audit.record(
            session, None, None, "user.create", f"Завёл администратора «{name}» ({login})",
            entity_type="user", entity_id=user.id, entity_name=name, actor_login=CLI,
        )  # fmt: skip
        await session.commit()
    print(f"Администратор {name} ({login}) заведён — войдите с этим логином и паролем")


async def set_password(login: str, password: str) -> None:
    login = login.strip().lower()
    await _ready()
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(func.lower(User.login) == login))
        if user is None:
            raise SystemExit(f"Сотрудника с логином {login} нет")
        user.password_hash = hash_password(password)
        audit.record(
            session, None, None, "user.password", f"Сменил пароль сотрудника «{user.name}» ({login})",
            entity_type="user", entity_id=user.id, entity_name=user.name, actor_login=CLI,
        )  # fmt: skip
        await session.commit()
    print(f"Пароль {user.name} ({login}) сменён" + ("" if user.is_active else ". Учётная запись отключена — включите её"))


async def install_methodology() -> None:
    await _ready()
    async with SessionLocal() as session:
        added = await methodology.install(session)
        await session.commit()
    print(f"Добавлено базовых правил: {added}" if added else "Все базовые правила уже на месте — ничего не изменено")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage", description="Служебные команды СтройКонтроля")
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="завести администратора (первого — в пустой базе)")
    admin.add_argument("--login", required=True)
    admin.add_argument("--name", required=True, help="фамилия и имя")
    admin.add_argument("--phone", default="")
    admin.add_argument("--password-stdin", action="store_true", help="прочитать пароль строкой со стандартного входа")
    password = commands.add_parser("set-password", help="сменить пароль сотрудника (например, забытый пароль администратора)")
    password.add_argument("--login", required=True)
    password.add_argument("--password-stdin", action="store_true", help="прочитать пароль строкой со стандартного входа")
    commands.add_parser("methodology", help="вернуть недостающие базовые правила «этап → техника» (изменённые не трогает)")
    args = parser.parse_args(argv)

    if args.command in ("create-admin", "set-password") and get_settings().auth_mode == "keycloak":
        # роль и пароль сотрудника тогда живут в Keycloak: администратор — пользователь Keycloak с ролью admin
        raise SystemExit("Вход через Keycloak: администратора и пароли заводят в Keycloak (роль admin), а не здесь")
    if args.command == "create-admin":
        run = create_admin(args.login, args.name, args.phone, _password(args.password_stdin))
    elif args.command == "set-password":
        run = set_password(args.login, _password(args.password_stdin))
    else:
        run = install_methodology()

    async def _run() -> None:
        try:
            await run
        finally:
            await engine.dispose()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
