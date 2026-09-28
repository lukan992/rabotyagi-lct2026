"""Рабочее время объекта: когда технику сверять и кадры сервисам аналитики отправлять.

Ночью и в выходные техники на площадке нет — это не «нет экскаватора» и не «простой». Вне рабочего времени сверка
техники не идёт: новые отклонения не заводятся, открытые не снимаются, серии подтверждения не продолжаются.
Камера без видео остаётся отклонением и ночью — зона не просматривается.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import Site

DAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
AROUND_THE_CLOCK = (0, 24, "1111111")


def working_now(site: Site, at: datetime) -> bool:
    """Идёт ли на объекте рабочее время в момент at (часы и дни — местные)."""
    local = at.astimezone(ZoneInfo(get_settings().timezone))
    if site.work_days[local.weekday()] != "1":
        return False
    hour = local.hour + local.minute / 60
    if site.work_from < site.work_to:
        return site.work_from <= hour < site.work_to
    if site.work_from > site.work_to:  # ночная смена: 20–8
        return hour >= site.work_from or hour < site.work_to
    return True  # 0–0 и 24–24 — круглые сутки


def describe(site: Site) -> str:
    """«круглосуточно», «8:00–20:00, пн–сб», «7:00–19:00, пн, ср, пт»."""
    if (site.work_from, site.work_to, site.work_days) == AROUND_THE_CLOCK or site.work_from == site.work_to:
        hours = "круглосуточно"
    else:
        hours = f"{site.work_from}:00–{site.work_to % 24}:00"
    days = [DAYS[n] for n, flag in enumerate(site.work_days) if flag == "1"]
    if len(days) == 7:
        return hours if hours == "круглосуточно" else f"{hours}, без выходных"
    first, last = site.work_days.find("1"), site.work_days.rfind("1")
    if days and site.work_days[first : last + 1] == "1" * (last - first + 1) and len(days) > 2:
        span = f"{DAYS[first]}–{DAYS[last]}"
    else:
        span = ", ".join(days) or "нет рабочих дней"
    return f"{hours}, {span}"
