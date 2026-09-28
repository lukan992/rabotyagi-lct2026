"""Демонстрационные данные.

Время считается от момента первого запуска, поэтому стенд всегда выглядит «сегодняшним».
Сегодняшний день проигрывается через настоящий движок сверки: кадры демо-камер приходят раунд за раундом,
и предупреждения создаёт та же логика, что работает с живыми камерами. История прошлых дней записывается готовой.

Пересоздать базу:  uv run python -m app.seed --reset
"""

import asyncio
import logging
import sys
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import dbschema
from app.config import get_settings
from app.db import SessionLocal, engine, utcnow
from app.mock_analytics import CATALOG_VERSION as ANALYTICS_CATALOG_VERSION
from app.models import (
    Alert,
    AlertEvent,
    Camera,
    Rule,
    Site,
    Snapshot,
    Stage,
    User,
    Zone,
    new_id,
)
from app.security import hash_password
from app.services import methodology, texts
from app.services.analysis import get_mock
from app.services.analytics.observations import record_observations
from app.services.camera_client import mock_frame
from app.services.engine import FIRST_ALERT_NUMBER, SYSTEM, TZ, current_stage, local_day, process_frame, run_check
from app.services.video import demo_feed_address

settings = get_settings()
log = logging.getLogger("stroykontrol.seed")
_PLAN_ANCHOR = date(2026, 9, 15)  # день, под который составлен демонстрационный календарный план

# ---------- справочники ----------
USERS = [  # id, логин, имя, роль, телефон, объекты
    ("u1", "prorab", "Кузнецов Андрей", "foreman", "+7 916 123-45-67", ["s1"]),
    ("u2", "prorab2", "Петров Сергей", "foreman", "+7 926 234-56-78", ["s2"]),
    ("u3", "prorab3", "Волков Игорь", "foreman", "+7 903 345-67-89", ["s3"]),
    ("u4", "prorab4", "Смирнова Ольга", "foreman", "+7 985 456-78-90", ["s4"]),
    ("u5", "rukovoditel", "Иванова Мария", "manager", "+7 916 567-89-01", ["s1", "s2", "s3", "s4"]),
    ("u6", "inspektor", "Соколов Дмитрий", "inspector", "+7 926 678-90-12", ["s1", "s2", "s3", "s4"]),
    ("u7", "admin", "Орлов Павел", "admin", "+7 903 789-01-23", []),
]

SITES = [  # id, название, адрес, подрядчик, прораб (выполнение объекта считается по его плану)
    ("s1", "ЖК «Северный парк», корпус 3", "ул. Дыбенко, вл. 7, САО", "ООО «МонолитСтрой»", "Кузнецов Андрей"),
    ("s2", "Школа на 550 мест", "ул. Лобачевского, 92, ЗАО", "АО «Стройтрест-11»", "Петров Сергей"),
    (
        "s3",
        "Реконструкция Дмитровского шоссе, участок 2",
        "Дмитровское ш., км 12–14, САО",
        "ГБУ «Автомобильные дороги»",
        "Волков Игорь",
    ),
    ("s4", "Детский сад на 250 мест", "ул. Рождественская, 21, Некрасовка", "ООО «ГорСтройКомплект»", "Смирнова Ольга"),
]

ZONES = [  # id, объект, название, вид
    ("z1-pit", "s1", "Котлован, оси А–Д", "work"),
    ("z1-gate", "s1", "Въезд и мойка колёс", "gate"),
    ("z1-yard", "s1", "Склад материалов", "storage"),
    ("z2-found", "s2", "Фундаментная плита, блок Б", "work"),
    ("z2-gate", "s2", "Въезд", "gate"),
    ("z3-road", "s3", "Полоса 1–2, ПК 12+00", "work"),
    ("z3-road2", "s3", "Полоса 3–4, ПК 13+50", "work"),
    ("z4-yard", "s4", "Стройплощадка, общий вид", "work"),
]

CAMERAS = [  # id, объект, зона, название, сцена, демо-ролик (None — камера «без сигнала»: её поток никто не публикует)
    ("c1", "s1", "z1-pit", "Камера 1 — котлован", "pit", "pit-excavator"),
    ("c2", "s1", "z1-gate", "Камера 2 — въезд", "entrance", "gate-crane"),
    ("c3", "s1", "z1-yard", "Камера 3 — склад", "yard", None),
    ("c4", "s2", "z2-found", "Камера 1 — фундамент", "foundation", "foundation-mixer"),
    ("c5", "s2", "z2-gate", "Камера 2 — въезд", "entrance", "gate-mixer"),
    ("c6", "s3", "z3-road", "Камера 1 — ПК 12", "road", "road-roller"),
    ("c7", "s3", "z3-road2", "Камера 2 — ПК 13", "road", "road-dumptruck"),
    ("c8", "s4", "z4-yard", "Камера 1 — общий вид", "yard", "yard-bulldozer"),
]

# Календарный план: (id, объект, родитель, уровень, название, начало, конец, правило) — даты относительно _PLAN_ANCHOR
STAGES = [
    ("s1-l1-prep", "s1", None, 1, "Подготовительный период", "2026-08-03", "2026-08-21", None),
    ("s1-prep", "s1", "s1-l1-prep", 2, "Подготовка площадки", "2026-08-03", "2026-08-21", "site_prep"),
    ("s1-l1-earth", "s1", None, 1, "Земляные работы", "2026-08-24", "2026-10-02", None),
    ("s1-excavation", "s1", "s1-l1-earth", 2, "Разработка котлована", "2026-08-24", "2026-09-25", "excavation"),
    ("s1-soil", "s1", "s1-l1-earth", 2, "Вывоз грунта", "2026-09-21", "2026-10-02", "soil_removal"),
    ("s1-l1-found", "s1", None, 1, "Нулевой цикл", "2026-10-05", "2026-11-27", None),
    ("s1-found", "s1", "s1-l1-found", 2, "Бетонирование фундаментной плиты", "2026-10-05", "2026-11-06", "foundation_concrete"),
    ("s1-backfill", "s1", "s1-l1-found", 2, "Обратная засыпка", "2026-11-09", "2026-11-27", "backfill"),
    ("s1-l1-frame", "s1", None, 1, "Надземная часть", "2026-11-30", "2027-05-14", None),
    ("s1-frame", "s1", "s1-l1-frame", 2, "Монтаж каркаса", "2026-11-30", "2027-05-14", "frame_assembly"),
    ("s2-l1-prep", "s2", None, 1, "Подготовительный период", "2026-06-01", "2026-06-19", None),
    ("s2-prep", "s2", "s2-l1-prep", 2, "Подготовка площадки", "2026-06-01", "2026-06-19", "site_prep"),
    ("s2-l1-earth", "s2", None, 1, "Земляные работы", "2026-06-22", "2026-08-14", None),
    ("s2-excavation", "s2", "s2-l1-earth", 2, "Разработка котлована", "2026-06-22", "2026-08-14", "excavation"),
    ("s2-l1-found", "s2", None, 1, "Нулевой цикл", "2026-08-17", "2026-10-30", None),
    (
        "s2-foundation",
        "s2",
        "s2-l1-found",
        2,
        "Бетонирование фундаментной плиты",
        "2026-08-17",
        "2026-10-09",
        "foundation_concrete",
    ),
    ("s2-backfill", "s2", "s2-l1-found", 2, "Обратная засыпка", "2026-10-12", "2026-10-30", "backfill"),
    ("s2-l1-frame", "s2", None, 1, "Надземная часть", "2026-11-02", "2027-04-30", None),
    ("s2-frame", "s2", "s2-l1-frame", 2, "Монтаж каркаса", "2026-11-02", "2027-04-30", "frame_assembly"),
    ("s3-l1-prep", "s3", None, 1, "Подготовительный период", "2026-07-06", "2026-07-17", None),
    ("s3-prep", "s3", "s3-l1-prep", 2, "Подготовка площадки", "2026-07-06", "2026-07-17", "site_prep"),
    ("s3-l1-base", "s3", None, 1, "Дорожная одежда", "2026-07-20", "2026-10-09", None),
    ("s3-base", "s3", "s3-l1-base", 2, "Устройство основания дороги", "2026-07-20", "2026-09-04", "road_base"),
    ("s3-asphalt", "s3", "s3-l1-base", 2, "Укладка асфальта", "2026-09-07", "2026-10-09", "asphalt"),
    ("s3-l1-land", "s3", None, 1, "Благоустройство", "2026-10-12", "2026-11-13", None),
    ("s3-land", "s3", "s3-l1-land", 2, "Благоустройство", "2026-10-12", "2026-11-13", "landscaping"),
    ("s4-l1-prep", "s4", None, 1, "Подготовительный период", "2026-09-01", "2026-09-25", None),
    ("s4-prep", "s4", "s4-l1-prep", 2, "Подготовка площадки", "2026-09-01", "2026-09-25", "site_prep"),
    ("s4-l1-earth", "s4", None, 1, "Земляные работы", "2026-09-28", "2026-11-06", None),
    ("s4-excavation", "s4", "s4-l1-earth", 2, "Разработка котлована", "2026-09-28", "2026-10-23", "excavation"),
    ("s4-soil", "s4", "s4-l1-earth", 2, "Вывоз грунта", "2026-10-19", "2026-11-06", "soil_removal"),
]


# Выполнение текущих работ по факту: на сколько процентов оно расходится с графиком (минус — отставание).
# ЖК заметно отстаёт — как и в его отклонениях; детский сад идёт чуть впереди графика.
SITE_KIND = {"s1": "housing", "s2": "education", "s3": "roads", "s4": "preschool"}  # как в «Справочнике видов работ»
FACT_SHIFT = {"s1": -15, "s2": -6, "s3": -7, "s4": 4}
# По графику работа должна была закончиться, а по вчерашней отметке руководителя ещё идёт: основание дороги доделывают.
# Сервис аналитики «по технике» покажет это как возможное отставание
LATE = {"s3-base": 95}

# Вид работ по справочнику сервисов аналитики коллеги (0.2.0, catalog-cf435bba…; тот же у имитации) для работ
# демо-плана — по их правилу. Выбран человеком: по названию и по технике в матрице коллеги, а не автоматически
CATALOG_WORKS = {
    "site_prep": 368,  # Вертикальная планировка — расчистка и планировка площадки бульдозером
    "excavation": 47,  # Устройство котлована
    "soil_removal": 72,  # Выемка грунта котлована — отдельного вывоза грунта в справочнике нет
    "backfill": 77,  # Обратная засыпка грунтом
    "foundation_concrete": 85,  # Устройство монолитной ж/б фундаментной плиты
    "frame_assembly": 182,  # Устройство ж/б конструкций (конкретная работа «Каркаса здания»)
    "road_base": 80,  # Устройство нижнего слоя основания дорожной одежды
    "asphalt": 149,  # Устройство нижнего слоя покрытия
    "landscaping": 367,  # Установка МАФов — благоустройство краном-манипулятором
}


def _h(hours: float = 0, days: int = 0, minutes: int = 0) -> timedelta:
    return timedelta(days=days, hours=hours, minutes=minutes)


async def _catalog(session: AsyncSession, today: date) -> None:
    password_hash = hash_password(settings.demo_password)
    sites = {}
    for pos, (sid, name, address, contractor, foreman) in enumerate(SITES):
        sites[sid] = Site(
            id=sid, name=name, address=address, contractor=contractor, foreman_name=foreman, kind=SITE_KIND[sid], position=pos
        )
    session.add_all(sites.values())
    await session.flush()  # объекты должны существовать до зон, камер и пользователей, которые на них ссылаются
    for uid, login, name, role, phone, site_ids in USERS:
        session.add(
            User(
                id=uid,
                login=login,
                name=name,
                role=role,
                phone=phone,
                password_hash=password_hash,
                sites=[sites[s] for s in site_ids],
            )
        )
    session.add_all(Zone(id=z, site_id=s, name=n, kind=k, position=p) for p, (z, s, n, k) in enumerate(ZONES))
    await session.flush()
    for p, (c, s, z, n, scene, clip) in enumerate(CAMERAS):
        feed = demo_feed_address(clip or "offline")  # обычный RTSP-адрес — как если бы камеру добавили через форму
        session.add(
            Camera(
                id=c,
                site_id=s,
                zone_id=z,
                name=n,
                scene=scene,
                position=p,
                source_type="rtsp",
                scheme=feed.scheme,
                host=feed.host,
                port=feed.port,
                path=feed.path,
            )  # fmt: skip
        )
    await methodology.install(session)  # та же базовая методика, что ставится в любую новую базу
    shift = today - _PLAN_ANCHOR  # сдвигаем план так, чтобы «сегодня» попадало на те же этапы
    for pos, (sid, site, parent, level, name, start, end, rule_key) in enumerate(STAGES):
        stage = Stage(
            id=sid,
            site_id=site,
            parent_id=parent,
            level=level,
            name=name,
            rule_key=rule_key,
            position=pos,
            start_date=date.fromisoformat(start) + shift,
            end_date=date.fromisoformat(end) + shift,
        )
        if level == 2:  # у укрупнённых этапов выполнение считается по их работам
            stage.catalog_stage_id, stage.catalog_version = CATALOG_WORKS[rule_key], ANALYTICS_CATALOG_VERSION
            if stage.end_date < today and sid in LATE:
                stage.fact_progress, stage.fact_updated_at = LATE[sid], utcnow() - _h(20)
            elif stage.end_date < today:
                stage.fact_progress = 100
                stage.fact_updated_at = datetime.combine(stage.end_date, time(18), TZ)  # закрыли в последний день
            elif stage.start_date <= today:
                stage.fact_progress = max(0, min(100, stage.plan_progress_on(today) + FACT_SHIFT[site]))
                stage.fact_updated_at = utcnow() - _h(18)  # руководитель отмечал вчера
        session.add(stage)
    await session.flush()


async def _frame(session: AsyncSession, camera_id: str, name: str, at: datetime) -> Snapshot:
    """Кадр из прошлого — демонстрационное фото. Разбираем именно фото, а не ролик, который камера показывает сейчас."""
    camera = await session.get(Camera, camera_id)
    jpeg = mock_frame(name)
    result = await get_mock().analyze_photo(jpeg, key=camera_id)
    return await process_frame(session, camera, jpeg, at=at, source="seed", image_url=f"/media/seed/{name}.jpg", result=result)


async def _past_alert(
    session: AsyncSession,
    *,
    number: int,
    camera_id: str,
    kind: str,
    equipment: str,
    expected: int | None,
    observed: int,
    frames: list[tuple[str, datetime]],
    severity: str,
    status: str,
    events: list[tuple[datetime, str, str, str | None]],
    cleared_at: datetime,
    resolved_at: datetime | None = None,
    prescription_no: str | None = None,
) -> None:
    """Предупреждение из прошлых дней — записывается готовым, тексты строятся теми же шаблонами, что у движка."""
    camera = await session.get(Camera, camera_id)
    snapshots = [await _frame(session, camera_id, name, at) for name, at in frames]
    start, end = frames[0][1], frames[-1][1]
    stage = await current_stage(session, camera.site_id, local_day(start))
    rule = await session.get(Rule, stage.rule_key)
    item = next((i for i in rule.items if i.equipment_type == equipment and i.kind != "allowed"), None)
    if kind in ("missing", "count_below"):
        words = texts.shortage(
            equipment=equipment,
            expected=expected,
            observed=observed,
            stage_name=stage.name,
            checks=len(frames),
            start=start,
            end=end,
            risk=item.risk if item else "",
        )
    elif kind == "unexpected":
        words = texts.unexpected(
            equipment=equipment,
            stage_name=stage.name,
            camera_name=camera.name,
            checks=len(frames),
            start=start,
            end=end,
            why=item.why if item else "",
            risk=item.risk if item else "",
        )
    else:
        words = texts.idle(equipment=equipment, camera_name=camera.name, start=start, end=end)
    alert = Alert(
        id=new_id("a"),
        number=number,
        site_id=camera.site_id,
        zone_id=camera.zone_id,
        stage_id=stage.id,
        camera_id=None if kind in ("missing", "count_below") else camera_id,
        kind=kind,
        severity=severity,
        status=status,
        equipment_type=equipment,
        expected=expected,
        observed=observed,
        started_at=start,
        updated_at=events[-1][0],
        cleared_at=cleared_at,
        resolved_at=resolved_at,
        prescription_no=prescription_no,
        **words.__dict__,
    )
    alert.evidence = snapshots
    alert.events = [AlertEvent(at=at, who=who, text=text, status=st) for at, who, text, st in events]
    session.add(alert)
    await session.flush()


async def _history(session: AsyncSession, t0: datetime) -> None:
    n = FIRST_ALERT_NUMBER
    auto = "Отклонение больше не наблюдается — снято автоматически."

    d = t0 - _h(days=6)
    await _past_alert(
        session,
        number=n,
        camera_id="c6",
        kind="count_below",
        equipment="roller",
        expected=2,
        observed=1,
        severity="high",
        frames=[("road-roller-a", d - _h(3)), ("road-roller-b", d - _h(2))],
        status="resolved",
        events=[
            (d - _h(2), SYSTEM, "Отклонение подтверждено: 2 проверки подряд.", "new"),
            (
                d - _h(1, minutes=40),
                "Волков Игорь (прораб)",
                "Техника едет. Второй каток на заправке, будет через час.",
                "acknowledged",
            ),
            (d - _h(1), SYSTEM, auto, "resolved"),
        ],
        cleared_at=d - _h(1),
        resolved_at=d - _h(1),
    )

    d = t0 - _h(days=5)
    await _past_alert(
        session,
        number=n + 1,
        camera_id="c1",
        kind="missing",
        equipment="dump_truck",
        expected=2,
        observed=0,
        severity="high",
        frames=[("pit-excavator", d - _h(4)), ("pit-excavator", d - _h(3)), ("pit-excavator", d - _h(2))],
        status="resolved",
        events=[
            (d - _h(2), SYSTEM, "Отклонение подтверждено: 3 проверки подряд.", "new"),
            (
                d - _h(1, minutes=40),
                "Кузнецов Андрей (прораб)",
                "Подтверждаю: самосвалы стоят в очереди на полигоне, разбираемся.",
                "confirmed",
            ),
            (d, "Иванова Мария (руководитель проекта)", "Вывоз возобновлён, договорились о втором полигоне.", "resolved"),
        ],
        cleared_at=d,
        resolved_at=d,
    )

    d = t0 - _h(days=4)
    await _past_alert(
        session,
        number=n + 2,
        camera_id="c8",
        kind="idle",
        equipment="bulldozer",
        expected=None,
        observed=1,
        severity="medium",
        frames=[("yard-bulldozer", d - _h(3)), ("yard-bulldozer", d - _h(2)), ("yard-bulldozer", d - _h(1))],
        status="false_positive",
        events=[
            (d - _h(1), SYSTEM, "Техника стоит на одном месте 2 часа.", "new"),
            (
                d - _h(0, minutes=30),
                "Смирнова Ольга (прораб)",
                "Это ошибка: бульдозер планировал въезд на одном пятачке, работа шла.",
                "false_positive",
            ),
        ],
        cleared_at=d - _h(0, minutes=30),
        resolved_at=d - _h(0, minutes=30),
    )

    d = t0 - _h(days=3)
    await _past_alert(
        session,
        number=n + 3,
        camera_id="c1",
        kind="count_below",
        equipment="dump_truck",
        expected=2,
        observed=1,
        severity="medium",
        frames=[("earthworks-loading", d - _h(3)), ("earthworks-loading", d - _h(2)), ("earthworks-loading", d - _h(1))],
        status="prescribed",
        prescription_no="114-П",
        events=[
            (d - _h(1), SYSTEM, "Отклонение подтверждено: 3 проверки подряд.", "new"),
            (
                d - _h(0, minutes=45),
                "Кузнецов Андрей (прораб)",
                "Подтверждаю: второй самосвал на ремонте, замена будет после обеда.",
                "confirmed",
            ),
            (
                d + _h(20),
                "Соколов Дмитрий (инспектор)",
                "Выдано предписание № 114-П: обеспечить резервную технику.",
                "prescribed",
            ),
        ],
        cleared_at=d + _h(2),
    )

    d = t0 - _h(days=2)
    await _past_alert(
        session,
        number=n + 4,
        camera_id="c4",
        kind="count_below",
        equipment="mixer",
        expected=2,
        observed=1,
        severity="medium",
        frames=[("foundation-pump", d - _h(5)), ("foundation-pump", d - _h(4))],
        status="resolved",
        events=[
            (d - _h(4), SYSTEM, "Отклонение подтверждено: 2 проверки подряд.", "new"),
            (
                d - _h(3, minutes=45),
                "Петров Сергей (прораб)",
                "Техника едет. Завод задержал отгрузку, второй миксер выехал.",
                "acknowledged",
            ),
            (d - _h(3), SYSTEM, auto, "resolved"),
        ],
        cleared_at=d - _h(3),
        resolved_at=d - _h(3),
    )
    await _past_alert(
        session,
        number=n + 5,
        camera_id="c7",
        kind="idle",
        equipment="dump_truck",
        expected=None,
        observed=1,
        severity="medium",
        frames=[("road-dumptruck", d - _h(4)), ("road-dumptruck", d - _h(3)), ("road-dumptruck", d - _h(2))],
        status="resolved",
        events=[
            (d - _h(2), SYSTEM, "Техника стоит на одном месте 2 часа.", "new"),
            (
                d - _h(1, minutes=50),
                "Волков Игорь (прораб)",
                "Подтверждаю: замена колеса, самосвал вернётся в работу через час.",
                "confirmed",
            ),
            (d - _h(1), SYSTEM, auto, "resolved"),
        ],
        cleared_at=d - _h(1),
        resolved_at=d - _h(1),
    )

    await _past_alert(
        session,
        number=n + 6,
        camera_id="c5",
        kind="unexpected",
        equipment="excavator",
        expected=0,
        observed=1,
        severity="low",
        frames=[("gate-grabtruck", t0 - _h(21.5)), ("gate-grabtruck", t0 - _h(20.5))],
        status="resolved",
        events=[
            (t0 - _h(20.5), SYSTEM, "Техника не по этапу видна 2 проверки подряд.", "new"),
            (
                t0 - _h(20, minutes=20),
                "Петров Сергей (прораб)",
                "Экскаватор приехал на дозачистку пазух по согласованию с заказчиком.",
                "confirmed",
            ),
            (t0 - _h(19.5), "Иванова Мария (руководитель проекта)", "Подтверждаю, работы согласованы. Закрыто.", "resolved"),
        ],
        cleared_at=t0 - _h(19.5),
        resolved_at=t0 - _h(19.5),
    )


async def _today(session: AsyncSession, t0: datetime) -> None:
    """Сегодняшние раунды съёмки. Предупреждения создаёт движок — ровно как при работе с живыми камерами."""
    rounds: list[tuple[timedelta, dict[str, dict[str, str]]]] = [
        (_h(4.5), {"s1": {"c1": "pit-excavator"}}),
        (_h(4, minutes=20), {"s1": {"c3": "yard-bulldozer"}}),
        (_h(3), {"s1": {"c1": "pit-excavator"}}),
        (
            _h(2),
            {
                "s1": {"c1": "pit-excavator", "c2": "gate-crane"},
                "s2": {"c4": "foundation-pump"},
                "s3": {"c6": "road-roller-a", "c7": "road-dumptruck"},
                "s4": {"c8": "yard-bulldozer"},
            },
        ),
        (
            _h(1),
            {
                "s1": {"c1": "pit-excavator", "c2": "gate-crane"},
                "s2": {"c4": "foundation-pump"},
                "s3": {"c6": "road-roller-b", "c7": "road-dumptruck"},
                "s4": {"c8": "yard-bulldozer"},
            },
        ),
        (
            _h(0),
            {
                "s1": {"c1": "pit-excavator", "c2": "gate-crane"},
                "s2": {"c4": "foundation-pump", "c5": "gate-mixer"},
                "s3": {"c6": "road-roller-b", "c7": "road-dumptruck"},
                "s4": {"c8": "yard-bulldozer"},
            },
        ),
    ]
    for back, sites in rounds:
        at = t0 - back
        for site_id, frames in sites.items():
            snapshots = [await _frame(session, camera_id, name, at) for camera_id, name in frames.items()]
            if "c3" in frames:  # склад: после этого кадра камера «пропадает» (её демо-поток никто не публикует)
                camera = await session.get(Camera, "c3")
                camera.status, camera.last_error = "offline", "Видео с камеры не приходит: нет сигнала"
            await session.flush()
            await record_observations(session, [(await session.get(Camera, s.camera_id), s) for s in snapshots])
            check = await run_check(session, site_id, at=at, trigger="seed")
            for snapshot in snapshots:
                snapshot.check_id = check.id

        if back == _h(1):  # прораб школы ответил на предупреждение о бетоносмесителях
            alert = await session.scalar(
                select(Alert).where(Alert.site_id == "s2", Alert.equipment_type == "mixer", Alert.status == "new")
            )
            if alert:
                alert.status = "acknowledged"
                alert.events.append(
                    AlertEvent(
                        at=at + _h(0, minutes=10),
                        who="Петров Сергей (прораб)",
                        status="acknowledged",
                        text="Техника едет. Второй миксер выехал с завода, будет в течение часа.",
                    )
                )


async def seed(session: AsyncSession) -> None:
    now = utcnow().replace(second=0, microsecond=0)
    t0 = now - timedelta(minutes=10)  # последний раунд съёмки был «10 минут назад»
    await _catalog(session, local_day(now))
    await _history(session, t0)
    await _today(session, t0)
    await session.commit()


async def seed_if_empty() -> bool:
    async with SessionLocal() as session:
        if await session.scalar(select(func.count()).select_from(User)):
            return False
        await seed(session)
        return True


async def _drop_everything() -> None:
    """Удалить все таблицы, какие есть в базе, — и те, что поздние миграции убрали из моделей. По моделям удалять нельзя:
    так в базе на 0007 оставалась stage_estimates (убрана в 0008), и её внешний ключ не давал удалить sites."""
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
        if settings.is_sqlite:
            # порядок удаления тогда не важен; прагма действует только вне транзакции — выполняем первой
            await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            for name in names:
                await conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        else:
            for name in names:
                await conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}" CASCADE')
        await conn.commit()


async def _migrate() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(dbschema.upgrade)


async def reset() -> None:
    """Пустая база по последней миграции + демонстрационные данные."""
    await _drop_everything()
    await _migrate()
    await seed_if_empty()


async def prepare_database(*, demo_data: bool | None = None) -> None:
    """Довести базу до последней миграции и, только при явном demo_data, наполнить пустую базу стендовыми данными.

    Без демонстрационных данных новая база получает базовую методику «этап → техника».
    Базу без отметки миграций никогда не изменяем автоматически.
    """
    async with engine.connect() as conn:
        legacy = await conn.run_sync(dbschema.created_before_migrations)
    if legacy:
        raise RuntimeError(
            "База создана версией без миграций. Сделайте резервную копию; если это версия 0.10 (в таблице app_meta "
            "записано schema = 2), удалите таблицу app_meta, выполните «uv run alembic stamp 0001» и перезапустите сервер. "
            "Базу более старой версии перенесите в новую вручную."
        )
    await _migrate()
    if (settings.seed_on_start if demo_data is None else demo_data) and await seed_if_empty():
        log.info("Пустая база наполнена демонстрационными данными")
        return
    async with SessionLocal() as session:
        if added := await methodology.install_if_unused(session):
            await session.commit()
            log.info("Новая база: поставлена базовая методика «этап → техника» (%d правил)", added)


if __name__ == "__main__":
    if "--reset" not in sys.argv:
        sys.exit("Запуск: python -m app.seed --reset  (пересоздаёт базу с демонстрационными данными)")
    asyncio.run(reset())
    print("База пересоздана и наполнена демонстрационными данными")
