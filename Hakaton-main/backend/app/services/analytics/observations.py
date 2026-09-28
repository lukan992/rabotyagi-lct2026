"""Журнал наблюдений — история для сервисов аналитики (раздел 7 контракта).

Сверка сохраняет кадр каждой камеры раз в минуту, но хранятся кадры несколько часов. Сервисам нужна неделя:
какая техника была видна и когда. Поэтому раз в analytics_observation_min минут запоминаем кадр без картинки:
id снимка, время, sha256 байтов кадра и найденную технику. Шаг 20 минут: сервису нужны точки не чаще 15 минут,
а в запросе их не больше 500 — ровно неделя одной камеры.
"""

import logging
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import utcnow
from app.models import Camera, Observation, Snapshot
from app.services.analytics.images import ImageProblem, load_image

log = logging.getLogger("stroykontrol.analytics")


def detections_of(snapshot: Snapshot) -> list[dict]:
    """Техника снимка для журнала: наши типы, рамка в процентах кадра; id — чтобы сервис ссылался на рамку."""
    return [
        {"id": d.id, "type": d.equipment_type, "confidence": round(d.confidence, 4), "box": [d.x, d.y, d.w, d.h]}
        for d in snapshot.detections
    ]


async def record_observations(session: AsyncSession, frames: list[tuple[Camera, Snapshot]]) -> None:
    """Записать в журнал свежие снимки камер, если с прошлой записи камеры прошло analytics_observation_min минут.
    Вызывается после flush: у рамок уже есть id."""
    step = timedelta(minutes=get_settings().analytics_observation_min)
    for camera, snapshot in frames:
        last = await session.scalar(select(func.max(Observation.observed_at)).where(Observation.camera_id == camera.id))
        if last is not None and snapshot.taken_at - last < step:
            continue
        try:
            image = load_image(snapshot.image_url)
        except ImageProblem as exc:
            log.warning("Кадр %s камеры %s не записан в журнал наблюдений: %s", snapshot.id, camera.id, exc)
            continue
        session.add(
            Observation(
                site_id=camera.site_id,
                camera_id=camera.id,
                image_id=snapshot.id,
                zone_kind=camera.zone.kind,
                observed_at=snapshot.taken_at,
                image_sha256=image.sha256,
                analyzed=snapshot.analyzed,
                provider=snapshot.provider,
                model=snapshot.model,
                detections=detections_of(snapshot),
            )
        )


async def cleanup_observations(session: AsyncSession) -> None:
    """Наблюдения старше окна истории сервисам не уходят."""
    keep = timedelta(days=get_settings().analytics_history_days)
    await session.execute(delete(Observation).where(Observation.observed_at < utcnow() - keep))
