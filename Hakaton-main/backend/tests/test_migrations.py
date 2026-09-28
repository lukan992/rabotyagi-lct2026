"""Миграции: строят ту же структуру, что описана в моделях, откатываются, и сервер правильно встречает старые базы."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, inspect, select, text

from app import dbschema, seed
from app.db import Base, SessionLocal, engine
from app.models import Camera, Site, User

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def dispose_engine():
    yield
    await engine.dispose()  # у каждого теста свой цикл событий — соединения PostgreSQL между ними не переносим


async def _run(fn, *args):  # noqa: ANN001, ANN202
    async with engine.begin() as conn:
        return await conn.run_sync(fn, *args)


def _schema_diff(connection) -> list:  # noqa: ANN001
    return compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)


def _tables(connection) -> set[str]:  # noqa: ANN001
    return set(inspect(connection).get_table_names())


async def _legacy_database() -> None:
    """База, какую создавала версия 0.10: таблицы без отметки Alembic, версия структуры — в app_meta."""
    await seed._drop_everything()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("CREATE TABLE app_meta (key VARCHAR(40) PRIMARY KEY, value VARCHAR(200))"))
        await conn.execute(text("INSERT INTO app_meta (key, value) VALUES ('schema', '2')"))
        await conn.execute(
            text(
                "INSERT INTO sites (id, name, address, contractor, foreman_name, position) "
                "VALUES ('legacy', 'Старый объект', '', '', '', 0)"
            )
        )


async def test_migrations_build_the_same_schema_as_models():
    await seed._drop_everything()
    await _run(dbschema.upgrade)
    assert await _run(dbschema.current_revision) == dbschema.head_revision()
    assert await _run(_schema_diff) == []  # модели и миграции не разошлись: иначе нужна новая миграция

    await _run(lambda conn: command.downgrade(dbschema.alembic_config(conn), "base"))
    assert await _run(_tables) <= {"alembic_version"}
    await _run(dbschema.upgrade)  # и снова вверх — миграции проходят в обе стороны
    assert await _run(_schema_diff) == []



async def test_0012_preserves_existing_cameras_and_enables_spider_for_them():
    await seed._drop_everything()
    await _run(lambda conn: command.upgrade(dbschema.alembic_config(conn), "0011"))
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO sites (id, name, address, contractor, foreman_name, kind, position, work_from, work_to, work_days) "
                "VALUES ('legacy-site', 'Старый объект', '', '', '', 'other', 0, 0, 24, '1111111')"
            )
        )
        await conn.execute(
            text("INSERT INTO zones (id, site_id, name, kind, position) VALUES ('legacy-zone', 'legacy-site', 'Зона', 'work', 0)")
        )
        await conn.execute(
            text(
                "INSERT INTO cameras (id, site_id, zone_id, name, source_type, scene, enabled, status, created_at, position) "
                "VALUES ('legacy-camera', 'legacy-site', 'legacy-zone', 'Старая камера', 'rtsp', 'yard', 1, 'unknown', "
                "'2026-09-27 00:00:00+00:00', 0)"
            )
        )
    await _run(dbschema.upgrade)
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT spider_enabled FROM cameras WHERE id = 'legacy-camera'"))).scalar_one() == 1

async def test_reset_removes_tables_dropped_by_later_migrations():
    """Сброс базы, отставшей на миграцию: таблица, которую поздняя миграция убрала из моделей (stage_estimates — в 0008),
    не мешает удалить остальные, хотя ссылается на них внешними ключами."""
    await seed._drop_everything()
    await _run(lambda conn: command.upgrade(dbschema.alembic_config(conn), "0007"))
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO sites (id, name, address, contractor, foreman_name, position, kind) VALUES ('s', 'Объект', '', '', '', 0, 'other')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO stages (id, site_id, level, name, start_date, end_date, position, fact_progress) "
                "VALUES ('st', 's', 2, 'Работа', '2026-09-01', '2026-09-30', 0, 0)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO stage_estimates (id, site_id, at, trigger, request_id, stage_id) VALUES ('se', 's', '2026-09-25 10:00:00+00:00', 'manual', 'r', 'st')"
            )
        )
    await seed.reset()
    assert "stage_estimates" not in await _run(_tables)
    assert await _run(dbschema.current_revision) == dbschema.head_revision()
    async with SessionLocal() as session:
        assert (await session.get(Site, "s1")).kind == "housing"  # демо-данные на месте


async def test_start_keeps_existing_data():
    await seed.reset()
    async with SessionLocal() as session:
        site = await session.get(Site, "s1")
        site.name = "Переименован до перезапуска"
        await session.commit()
    await seed.prepare_database()  # перезапуск сервера: база уже по последней миграции
    async with SessionLocal() as session:
        assert (await session.get(Site, "s1")).name == "Переименован до перезапуска"


async def test_legacy_database_is_never_recreated_even_in_demo_mode(monkeypatch):
    await _legacy_database()
    monkeypatch.setattr(seed.settings, "demo_mode", True)
    with pytest.raises(RuntimeError, match="alembic stamp 0001"):
        await seed.prepare_database()
    async with SessionLocal() as session:
        assert (await session.get(Site, "legacy")).name == "Старый объект"


async def test_production_database_from_before_migrations_is_left_alone(monkeypatch):
    await _legacy_database()
    monkeypatch.setattr(seed.settings, "demo_mode", False)
    with pytest.raises(RuntimeError, match="alembic stamp 0001"):
        await seed.prepare_database()
    async with SessionLocal() as session:
        assert (await session.get(Site, "legacy")).name == "Старый объект"  # боевые данные не тронуты


async def test_new_database_without_demo_data_gets_base_methodology(monkeypatch):
    from app.models import Rule
    from app.services.methodology import RULES

    await seed._drop_everything()
    monkeypatch.setattr(seed.settings, "seed_on_start", False)
    await seed.prepare_database()  # обычный первый запуск: методика есть, стендовых записей нет
    async with SessionLocal() as session:
        assert set(await session.scalars(select(Rule.key))) == {key for key, *_ in RULES}
        assert await session.scalar(select(func.count()).select_from(Site)) == 0
        assert await session.scalar(select(func.count()).select_from(Camera)) == 0
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        # администратор начал работу и удалил базовое правило — при перезапуске оно само не возвращается
        session.add(User(id="u-admin", login="admin", name="Администратор", role="admin", password_hash=""))
        await session.delete(await session.get(Rule, "asphalt"))
        await session.commit()
    await seed.prepare_database()
    async with SessionLocal() as session:
        assert await session.get(Rule, "asphalt") is None
