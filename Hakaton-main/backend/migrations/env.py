"""Окружение Alembic: адрес базы и описание таблиц берутся из приложения.

Миграции запускает сам сервер при старте (app/dbschema.py) — тогда он передаёт готовое соединение, — и командная
строка (`uv run alembic …`) — тогда соединение открывается здесь.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

import app.models  # noqa: F401 — регистрирует все таблицы в метаданных
from app.db import Base, UTCDateTime, engine

config = context.config
if config.config_file_name is not None:  # только из командной строки: у сервера журнал настроен своим порядком
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_: str, obj, _context):  # noqa: ANN001
    """Время в миграциях — обычный DateTime(timezone=True): файлы миграций не должны зависеть от кода приложения."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=render_item,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite меняет столбцы только пересозданием таблицы
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_with_own_connection() -> None:
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():  # alembic upgrade --sql: только напечатать SQL
    context.configure(url=engine.url, target_metadata=target_metadata, render_item=render_item, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    asyncio.run(run_with_own_connection())
