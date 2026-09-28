"""Тексты предупреждений простым языком.

Требование ТЗ: предупреждение должно быть содержательным, понятным и обоснованным.
Каждое предупреждение отвечает на четыре вопроса: что случилось, чем это грозит, что делать
и почему система так решила (последнее собирает интерфейс из правила, этапа и кадров).

«Что делать» — одинаково для всех ролей и без названий кнопок: какие кнопки есть у прораба,
руководителя или инспектора, интерфейс подсказывает сам.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.equipment import EQUIPMENT, at_least  # noqa: F401 — EQUIPMENT нужен движку

_TZ = ZoneInfo(get_settings().timezone)
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


@dataclass
class AlertTexts:
    title: str
    summary: str
    consequence: str
    advice: str


def hhmm(dt: datetime) -> str:
    return dt.astimezone(_TZ).strftime("%H:%M")


def day_month(dt: datetime) -> str:
    """«22 сентября» по местному времени."""
    local = dt.astimezone(_TZ)
    return f"{local.day} {_MONTHS[local.month - 1]}"


def _same_day(a: datetime, b: datetime) -> bool:
    return a.astimezone(_TZ).date() == b.astimezone(_TZ).date()


def plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and not 10 <= m100 < 20:
        return few
    return many


def duration(delta: timedelta) -> str:
    """«2 часа», «45 минут», «3 дня» — округляем вниз до понятной величины."""
    minutes = max(int(delta.total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')}"
    days = hours // 24
    return f"{days} {plural(days, 'день', 'дня', 'дней')}"


def _checks(n: int) -> str:
    return "на последней проверке" if n <= 1 else f"на {n} последних проверках"


# Короче — говорим о проверках («на 3 проверках подряд»), дольше — о времени: «6 часов подряд» понятнее, чем «на 56 проверках»
_LONG_ENOUGH = timedelta(minutes=30)


def _for_how_long(checks: int, start: datetime, end: datetime) -> str:
    """«6 часов подряд (с 15:07 до 21:43)» или «на 3 последних проверках (с 16:37 до 16:39)»."""
    if end - start >= _LONG_ENOUGH:
        return f"{duration(end - start)} подряд ({_period(start, end)})"
    return f"{_checks(checks)} ({_period(start, end)})"


def _period(start: datetime, end: datetime) -> str:
    if not _same_day(start, end):  # за несколько дней без дат выходило «с 17:55 до 17:14» — будто конец раньше начала
        return f"с {day_month(start)} {hhmm(start)} до {day_month(end)} {hhmm(end)}"
    return f"в {hhmm(end)}" if hhmm(start) == hhmm(end) else f"с {hhmm(start)} до {hhmm(end)}"


def shortage(
    *, equipment: str, expected: int, observed: int, stage_name: str, checks: int, start: datetime, end: datetime, risk: str
) -> AlertTexts:
    eq = EQUIPMENT[equipment]
    if observed == 0:
        title = f"Нет {eq.gen_pl} на этапе «{stage_name}»"
        seen = f"Камеры не видят в рабочей зоне ни одного — {_for_how_long(checks, start, end)}"
    else:
        title = f"Мало {eq.gen_pl}: {observed} из {expected}"
        seen = f"Камеры видят в рабочей зоне только {observed} — {_for_how_long(checks, start, end)}"
    summary = f"По плану идёт этап «{stage_name}», для него нужно {at_least(expected, eq)}. {seen}."
    return AlertTexts(
        title=title,
        summary=summary,
        consequence=risk or "Работы этапа идут медленнее плана — возможен срыв сроков.",
        advice=f"Выяснить у подрядчика, где {eq.nom_pl} и когда они будут на площадке.",
    )


def unexpected(
    *, equipment: str, stage_name: str, camera_name: str, checks: int, start: datetime, end: datetime, why: str, risk: str
) -> AlertTexts:
    eq = EQUIPMENT[equipment]
    reason = f" {why.rstrip('.')}." if why else ""
    return AlertTexts(
        title=f"{eq.name} не по этапу «{stage_name}»",
        summary=(
            f"Камера «{camera_name}» видит технику, которой на этапе «{stage_name}» быть не должно: "
            f"{eq.name.lower()} — {_for_how_long(checks, start, end)}.{reason}"
        ),
        consequence=risk or "Возможно, начаты работы не по графику или техника заехала по ошибке.",
        advice=f"Выяснить, зачем на площадке {eq.name.lower()} и согласован ли его заезд.",
    )


def idle(*, equipment: str, camera_name: str, start: datetime, end: datetime) -> AlertTexts:
    eq = EQUIPMENT[equipment]
    return AlertTexts(
        title=f"{eq.name} стоит без движения {duration(end - start)}",
        summary=(
            f"{eq.name} на камере «{camera_name}» стоит в одной точке {_period(start, end)}: на всех кадрах за это время "
            "положение не меняется."
        ),
        consequence="Возможен простой или поломка. Оплаченные машино-часы уходят впустую.",
        advice="Выяснить у машиниста причину простоя.",
    )


def camera_offline(*, camera_name: str, zone_name: str, last_snapshot: datetime | None, now: datetime) -> AlertTexts:
    if last_snapshot:
        at = hhmm(last_snapshot) if _same_day(last_snapshot, now) else f"{day_month(last_snapshot)} {hhmm(last_snapshot)}"
        summary = f"Видео с камеры не приходит с {at}. Уже {duration(now - last_snapshot)} нет данных."
    else:
        summary = "С момента подключения от камеры не пришло видео."
    return AlertTexts(
        title=f"{camera_name} не отвечает",
        summary=summary,
        consequence=f"Система не видит зону «{zone_name}». Отклонения в ней обнаружить нельзя.",
        advice="Проверить питание и интернет у камеры или обратиться в техподдержку.",
    )
