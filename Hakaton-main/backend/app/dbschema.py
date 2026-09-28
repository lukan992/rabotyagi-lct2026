"""Структура базы ведётся миграциями Alembic (backend/migrations).

Сервер при запуске сам доводит базу до последней миграции: новая база создаётся миграциями с нуля, у существующей
применяются только недостающие. Изменили модели (app/models.py) — нужна новая миграция:
    uv run alembic revision --autogenerate -m "что поменялось"
Функции синхронные: вызывать через AsyncConnection.run_sync — так миграции идут в том же соединении и транзакции.
"""

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.config import BASE_DIR

MIGRATIONS_DIR = BASE_DIR / "migrations"
LEGACY_TABLES = ("app_meta", "alembic_version")  # версия структуры до миграций и отметка самого Alembic


def alembic_config(connection: Connection | None = None) -> Config:
    # без alembic.ini: его раздел логирования перенастроил бы журнал работающего сервера
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def upgrade(connection: Connection) -> None:
    """Применить недостающие миграции."""
    command.upgrade(alembic_config(connection), "head")


def current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def created_before_migrations(connection: Connection) -> bool:
    """Таблицы есть, а отметки Alembic нет — базу создала версия без миграций."""
    tables = set(inspect(connection).get_table_names())
    return "users" in tables and "alembic_version" not in tables
