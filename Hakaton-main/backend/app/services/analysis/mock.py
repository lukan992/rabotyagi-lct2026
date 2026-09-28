"""Демо-анализатор: заменяет модель распознавания, пока её нет.

  • Кадры демо-камер: камера показывает известный ролик (assets/clips) — отдаём разметку этого ролика.
  • Фото (ручная проверка, страница сверки, история демо-базы): узнаём демонстрационные фото по «отпечатку»
    изображения (average hash 16×16) — работает и с пересжатыми копиями.
Незнакомый кадр честно помечается как необработанный — выдуманных рамок на чужих камерах и фото не рисуем.
"""

import io
import json
import time
from collections import defaultdict
from datetime import datetime

from PIL import Image

from app.config import ASSETS_DIR
from app.services.analysis.base import AnalysisResult, DetectedObject, clamp_box

SEED_DIR = ASSETS_DIR / "seed"
CLIPS_DIR = ASSETS_DIR / "clips"
_HASH_SIDE = 16
_MATCH_DISTANCE = 12  # из 256 бит: пересжатый тот же кадр отличается на ≤3, разные кадры — на ≥32
# Работающая техника смещается: четыре положения по кругу, соседние отличаются на 8% размера рамки (IoU ≈ 0.85)
_JITTER = [(0.04, 0.04), (-0.04, 0.04), (-0.04, -0.04), (0.04, -0.04)]


def fingerprint(image_bytes: bytes) -> int:
    with Image.open(io.BytesIO(image_bytes)) as img:
        small = img.convert("L").resize((_HASH_SIDE, _HASH_SIDE), Image.Resampling.BILINEAR)
        pixels = small.tobytes()  # режим «L»: один байт на пиксель
    mean = sum(pixels) / len(pixels)
    bits = 0
    for p in pixels:
        bits = (bits << 1) | (p > mean)
    return bits


class MockAnalyzer:
    name = "mock"

    def __init__(self) -> None:
        raw = json.loads((SEED_DIR / "annotations.json").read_text(encoding="utf-8"))
        self.annotations: dict[str, list[dict]] = {k: v for k, v in raw.items() if not k.startswith("_")}
        self.fingerprints: dict[str, int] = {
            name: fingerprint((SEED_DIR / f"{name}.jpg").read_bytes()) for name in self.annotations
        }
        clips_file = CLIPS_DIR / "annotations.json"
        clips = json.loads(clips_file.read_text(encoding="utf-8")) if clips_file.is_file() else {}
        self.clip_annotations: dict[str, list[dict]] = {k: v for k, v in clips.items() if not k.startswith("_")}
        self._camera_clips: dict[str, str | None] = {}
        self._tick: dict[str, int] = defaultdict(int)  # счётчик кадров по камерам — для смещения рамок

    def bind_camera(self, camera_id: str, clip: str | None) -> None:
        """Конвейер видео сообщает, какой демо-ролик показывает камера (None — настоящая камера)."""
        self._camera_clips[camera_id] = clip

    def identify(self, image: bytes) -> str | None:
        fp = fingerprint(image)
        name, distance = min(((n, (fp ^ h).bit_count()) for n, h in self.fingerprints.items()), key=lambda t: t[1])
        return name if distance <= _MATCH_DISTANCE else None

    async def analyze(self, image: bytes, *, camera_id: str | None = None, taken_at: datetime | None = None) -> AnalysisResult:
        clip = self._camera_clips.get(camera_id) if camera_id else None
        if clip in self.clip_annotations:
            return self._result(self.clip_annotations[clip], f"mock:clip:{clip}", camera_id, time.perf_counter())
        return await self.analyze_photo(image, key=camera_id)

    async def analyze_photo(self, image: bytes, *, key: str | None = None) -> AnalysisResult:
        """Только по «отпечатку» фото, без привязки камеры к ролику (ручная проверка, история демо-базы)."""
        started = time.perf_counter()
        try:
            name = self.identify(image)
        except OSError:
            name = None
        if name is None:
            return AnalysisResult(
                provider=self.name,
                supported=False,
                note="Демо-анализатор знает только демонстрационные ролики и фото. "
                "Для своих камер подключите сервис анализа (SK_ANALYSIS_PROVIDER=http).",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        return self._result(self.annotations[name], f"mock:{name}", key, started)

    def _result(self, items: list[dict], model: str, key: str | None, started: float) -> AnalysisResult:
        key = key or "-"
        step = self._tick[key]
        self._tick[key] += 1
        dx, dy = _JITTER[step % len(_JITTER)]
        wobble = ((step * 7) % 5 - 2) / 100  # уверенность слегка «дышит»: ±0.02

        detections = []
        for item in items:
            x, y, w, h = item["box"]
            if item.get("moving"):
                x, y = x + dx * w, y + dy * h
            x, y, w, h = clamp_box(x, y, w, h)
            confidence = round(min(max(item["confidence"] + wobble, 0.5), 0.99), 2)
            detections.append(DetectedObject(item["type"], confidence, x, y, w, h))
        return AnalysisResult(
            provider=self.name,
            detections=detections,
            model=model,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
