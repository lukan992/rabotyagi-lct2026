"""Сколько работала техника — по рамкам в реальном времени: своей модели (видео — до 8 раз в секунду, а камеры, которые
никто не смотрит, — раз в 2 с по анализу кадров) или внешнего сервиса разметки (10–15 сообщений в секунду на камеру).

По каждой камере и типу техники копим за час: сколько секунд тип был в кадре, сколько из них двигался и сколько
машин было одновременно. Раз в минуту накопленное дописывается в базу (таблица equipment_usage).
Это история для сервиса, определяющего этап по кадрам: «экскаватор работал 6 часов, бетоносмесителей не было».

Движение — по центру рамки трека: раз в 5 секунд сравниваем с прошлым положением; сдвинулся больше чем на 1 %
кадра — считаем, что машина двигалась эти 5 секунд. Дрожание рамки детектора (доли процента) движением не считается.
"""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import utcnow
from app.models import Camera, EquipmentUsage

settings = get_settings()

MAX_GAP_S = 2.0  # сообщений по камере не было дольше — промежуток не засчитываем (сервис или камера молчали)
MOVE_SAMPLE_S = 5.0
MOVE_MIN = 1.0  # % кадра
FORGET_TRACK_S = 60.0


def max_gap() -> float:
    """Промежуток между сообщениями, который ещё засчитываем. Рамки из анализа кадров приходят раз в frame_interval_s
    (камеру никто не смотрит) — с запасом на неровный шаг: иначе каждый промежуток обрезался бы до 2 с."""
    return max(MAX_GAP_S, settings.frame_interval_s * 1.5)


@dataclass
class Cell:
    present_s: float = 0.0
    moving_s: float = 0.0
    max_count: int = 0


@dataclass
class _Track:
    at: float
    cx: float
    cy: float
    moving: bool = False


class UsageMeter:
    def __init__(self) -> None:
        self.cells: dict[tuple[str, datetime, str], Cell] = {}
        self._last: dict[str, float] = {}  # камера → когда пришло прошлое сообщение
        self._tracks: dict[tuple[str, str], _Track] = {}

    def add(self, message: dict, now: float, wall: datetime) -> None:
        """message — сообщение сервиса в нашем виде (tracks.normalize); now — монотонное время, wall — UTC."""
        camera = message["cameraId"]
        dt = min(max(now - self._last.get(camera, now), 0.0), max_gap())
        self._last[camera] = now
        hour = wall.replace(minute=0, second=0, microsecond=0)
        counts: Counter[str] = Counter()
        moving: set[str] = set()
        for obj in message["objects"]:
            counts[obj["type"]] += 1
            box = obj["box"]
            cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
            key = (camera, obj["trackId"])
            if ":~" in obj["trackId"]:
                continue  # запасной номер (у сервиса нет track_id): это не одна и та же машина — движение не оцениваем
            track = self._tracks.get(key)
            if track is None:
                self._tracks[key] = _Track(now, cx, cy)
            elif now - track.at >= MOVE_SAMPLE_S:
                self._tracks[key] = _Track(now, cx, cy, math.hypot(cx - track.cx, cy - track.cy) >= MOVE_MIN)
            if self._tracks[key].moving:
                moving.add(obj["type"])
        for kind, n in counts.items():
            cell = self.cells.setdefault((camera, hour, kind), Cell())
            cell.present_s += dt
            cell.max_count = max(cell.max_count, n)
            if kind in moving:
                cell.moving_s += dt

    def take(self, now: float) -> dict[tuple[str, datetime, str], Cell]:
        """Забрать накопленное (и забыть давно пропавшие треки)."""
        cells, self.cells = self.cells, {}
        self._tracks = {k: t for k, t in self._tracks.items() if now - t.at < FORGET_TRACK_S}
        return cells

    def restore(self, cells: dict[tuple[str, datetime, str], Cell]) -> None:
        """Вернуть забранное, если записать не удалось: сложится с тем, что накопилось за это время."""
        for key, cell in cells.items():
            mine = self.cells.setdefault(key, Cell())
            mine.present_s += cell.present_s
            mine.moving_s += cell.moving_s
            mine.max_count = max(mine.max_count, cell.max_count)


async def save(session: AsyncSession, cells: dict[tuple[str, datetime, str], Cell]) -> int:
    """Дописать накопленное в базу: к строке того же часа, камеры и типа — прибавить. Удалённые камеры пропускаем.

    Прибавляет сама база (INSERT … ON CONFLICT DO UPDATE): так верно и при нескольких процессах сервера сразу —
    «прочитать, сложить, записать» в Python потеряло бы или удвоило чужую запись.
    """
    if not cells:
        return 0
    camera_ids = {camera_id for camera_id, _, _ in cells}
    cameras = {
        c.id: c for c in await session.scalars(select(Camera).where(Camera.id.in_(camera_ids), Camera.deleted_at.is_(None)))
    }
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    saved = 0
    for (camera_id, hour, kind), cell in cells.items():
        camera = cameras.get(camera_id)
        if camera is None or cell.present_s <= 0 and cell.max_count == 0:
            continue
        row = insert(EquipmentUsage).values(
            site_id=camera.site_id, camera_id=camera_id, zone_kind=camera.zone.kind, hour=hour, equipment_type=kind,
            max_count=cell.max_count, present_s=cell.present_s, moving_s=cell.moving_s,
        )  # fmt: skip
        table = EquipmentUsage.__table__.c
        await session.execute(
            row.on_conflict_do_update(
                index_elements=[table.camera_id, table.hour, table.equipment_type],
                set_={
                    "present_s": table.present_s + row.excluded.present_s,
                    "moving_s": table.moving_s + row.excluded.moving_s,
                    "max_count": func.max(table.max_count, row.excluded.max_count)
                    if session.bind.dialect.name == "sqlite"
                    else func.greatest(table.max_count, row.excluded.max_count),
                },
            )
        )
        saved += 1
    await session.commit()
    return saved


async def cleanup_usage(session: AsyncSession) -> None:
    """Удалить учёт старше keep_usage_days."""
    await session.execute(delete(EquipmentUsage).where(EquipmentUsage.hour < utcnow() - timedelta(days=settings.keep_usage_days)))
    await session.commit()
