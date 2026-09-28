"""Общий интерфейс анализа кадров.

Сейчас используется демо-анализатор (mock). Позже его заменит внешний сервис распознавания:
достаточно выставить SK_ANALYSIS_PROVIDER=http и SK_ANALYSIS_API_URL — остальной код не меняется.
"""

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.equipment import EQUIPMENT_TYPES


@dataclass
class DetectedObject:
    type: str  # один из app.equipment.EQUIPMENT_TYPES
    confidence: float  # 0..1
    x: float  # рамка в процентах от кадра, начало координат слева сверху
    y: float
    w: float
    h: float


@dataclass
class AnalysisResult:
    provider: str
    detections: list[DetectedObject] = field(default_factory=list)
    supported: bool = True  # False — анализатор не смог обработать кадр; сверку по такому кадру не проводим
    model: str | None = None
    note: str | None = None
    elapsed_ms: int = 0


class AnalysisError(Exception):
    """Сервис анализа недоступен или вернул непонятный ответ."""


class AnalysisProvider(Protocol):
    name: str

    async def analyze(
        self, image: bytes, *, camera_id: str | None = None, taken_at: datetime | None = None
    ) -> AnalysisResult: ...


def clamp_box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    """Рамка не должна выходить за кадр."""
    w = min(max(w, 0.5), 100.0)
    h = min(max(h, 0.5), 100.0)
    x = min(max(x, 0.0), 100.0 - w)
    y = min(max(y, 0.0), 100.0 - h)
    return round(x, 2), round(y, 2), round(w, 2), round(h, 2)


class DetectionError(ValueError):
    """Объект от внешнего сервиса не подходит под контракт — текст объясняет, что не так (его увидит разработчик сервиса)."""


class UnknownType(DetectionError):
    """Не наша техника (человек, легковушка): такие объекты просто пропускаем."""


def read_detection(item: object) -> tuple[str, float, float, float, float, float]:
    """Объект {"type", "confidence", "box": {"x", "y", "w", "h"}} → (тип, уверенность, x, y, w, h) в процентах кадра.

    Частые ошибки интеграции ловим явно, а не молча превращаем в неправильную рамку:
    рамка в долях 0–1 (так отдаёт YOLO) стала бы точкой в углу, рамка в пикселях — растянулась бы на весь кадр,
    уверенность в процентах (93) — выглядела бы как 100 %.
    """
    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
        raise DetectionError("объект должен быть JSON-объектом с полем type")
    kind = item["type"]
    if kind not in EQUIPMENT_TYPES:
        raise UnknownType(f"тип «{kind}» — не техника из списка")
    try:
        confidence = float(item.get("confidence", 0))
    except (TypeError, ValueError):
        raise DetectionError(f"confidence должно быть числом, а пришло {item.get('confidence')!r}") from None
    if not 0 <= confidence <= 1:  # NaN не проходит ни одно сравнение
        hint = " — похоже на проценты, нужна доля от 0 до 1" if 1 < confidence <= 100 else ""
        raise DetectionError(f"confidence = {item.get('confidence')!r}{hint}")
    box = item.get("box")
    try:
        x, y, w, h = (float(box[k]) for k in "xywh")  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        raise DetectionError('box должен быть {"x", "y", "w", "h"} с числами') from None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        raise DetectionError(f"рамка без размера или с нечисловыми значениями: {json.dumps(box)}")
    if max(x, y, w, h) > 100.5:
        raise DetectionError(f"рамка {json.dumps(box)} больше 100 — похоже на пиксели; нужны проценты от кадра 0–100")
    if max(x, y, w, h) <= 1:
        raise DetectionError(
            f"рамка {json.dumps(box)} в долях 0–1 (как у YOLO) — нужны проценты 0–100, x и y — левый верхний угол"
        )
    return (kind, confidence, *clamp_box(x, y, w, h))


FRAME_ASPECT = 16 / 9  # кадры хранятся дополненными полями до 16:9, и видео в плеере вписано так же


def pad_box(x: float, y: float, w: float, h: float, width: float, height: float) -> tuple[float, float, float, float]:
    """Рамка в процентах исходного кадра width×height → в процентах того же кадра, дополненного полями до 16:9.

    Сервер дополняет кадры до 16:9 чёрными полями по центру (как ImageOps.pad и ffmpeg pad), а плеер так же вписывает
    видео в рамку 16:9. Сервис разметки считает рамки по исходному кадру камеры — у камеры 4:3 без пересчёта
    рамки уехали бы вбок.
    """
    if width <= 0 or height <= 0 or abs(width / height - FRAME_ASPECT) < 0.01:
        return x, y, w, h
    aspect = width / height
    if aspect < FRAME_ASPECT:  # кадр уже 16:9 — поля слева и справа
        scale = aspect / FRAME_ASPECT
        return round((1 - scale) * 50 + x * scale, 2), y, round(w * scale, 2), h
    scale = FRAME_ASPECT / aspect  # кадр шире — поля сверху и снизу
    return x, round((1 - scale) * 50 + y * scale, 2), w, round(h * scale, 2)
