"""Подключение к базе: асинхронный SQLAlchemy 2. SQLite для локального запуска, PostgreSQL в Docker."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.config import get_settings

settings = get_settings()

if settings.is_sqlite:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


if settings.is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
        """WAL + ожидание блокировки: фоновый опрос камер и запросы API пишут в базу одновременно."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=8000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class UTCDateTime(TypeDecorator):
    """Время всегда хранится в UTC и всегда возвращается с часовым поясом.

    SQLite не хранит пояс, поэтому без этой обёртки из базы приходили бы «наивные» даты.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("В базу можно писать только время с часовым поясом")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# У каждого ограничения — имя (миграции ссылаются на ограничения по имени), и такое же, какое дал бы PostgreSQL
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def utcnow() -> datetime:
    return datetime.now(UTC)
