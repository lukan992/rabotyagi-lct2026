"""Анализ кадров своей моделью (SK_ANALYSIS_PROVIDER=local или auto): YOLO прямо в сервере, без внешнего сервиса."""

import time
from datetime import datetime
from typing import TYPE_CHECKING

from app.services.analysis.base import AnalysisError, AnalysisResult

if TYPE_CHECKING:  # модуль модели сам импортирует пакет analysis — прямой импорт замкнул бы круг
    from app.services.detector import Detector


class LocalAnalyzer:
    name = "local"

    def __init__(self, detector: "Detector") -> None:
        self.detector = detector

    async def analyze(self, image: bytes, *, camera_id: str | None = None, taken_at: datetime | None = None) -> AnalysisResult:
        started = time.perf_counter()
        try:
            detections = await self.detector.run(self.detector.detect_jpeg, image)
        except Exception as exc:  # битый кадр или сбой модели: этот кадр — «не разобран», сверка его пропустит
            raise AnalysisError(f"Модель не разобрала кадр: {exc}") from exc
        return AnalysisResult(
            provider=self.name,
            detections=detections,
            model=self.detector.name,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
