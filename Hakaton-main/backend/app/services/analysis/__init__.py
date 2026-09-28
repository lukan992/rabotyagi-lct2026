"""Выбор анализатора кадров по настройкам."""

from functools import lru_cache

from app.config import get_settings
from app.equipment import EQUIPMENT_TYPES
from app.services.analysis.base import AnalysisError, AnalysisProvider, AnalysisResult, DetectedObject
from app.services.analysis.http import HttpAnalyzer
from app.services.analysis.local import LocalAnalyzer
from app.services.analysis.mock import MockAnalyzer

__all__ = [
    "AnalysisError",
    "AnalysisProvider",
    "AnalysisResult",
    "DetectedObject",
    "LocalAnalyzer",
    "detectable_types",
    "get_analyzer",
    "get_mock",
    "get_photo_analyzer",
    "provider_name",
]


@lru_cache
def get_mock() -> MockAnalyzer:
    return MockAnalyzer()


@lru_cache
def get_analyzer() -> AnalysisProvider:
    settings = get_settings()
    if settings.analysis_provider == "http":
        if not settings.analysis_api_url:
            raise RuntimeError("SK_ANALYSIS_PROVIDER=http требует SK_ANALYSIS_API_URL")
        return HttpAnalyzer(settings.analysis_api_url, settings.analysis_api_key, settings.analysis_timeout_s)
    if settings.analysis_provider in ("local", "auto"):
        from app.services.detector import get_detector

        if (detector := get_detector()) is not None:  # auto без файла модели — демо-анализатор
            return LocalAnalyzer(detector)
    return get_mock()


class _UnavailablePhotoAnalyzer:
    """Push mode has no pull analyzer; uploads must never fall back to demo detections."""

    name = "local"

    async def analyze(self, image: bytes, *, camera_id=None, taken_at=None) -> AnalysisResult:  # noqa: ANN001
        return AnalysisResult(
            provider=self.name,
            supported=False,
            note="Локальная модель распознавания для загруженного фото недоступна.",
        )


def get_photo_analyzer() -> AnalysisProvider:
    """Analyzer for a user upload; live push detections cannot be fabricated for an independent still image."""
    if get_settings().analysis_provider != "push":
        return get_analyzer()
    from app.services.detector import get_detector

    detector = get_detector()
    return LocalAnalyzer(detector) if detector is not None else _UnavailablePhotoAnalyzer()


def detectable_types() -> frozenset[str]:
    """Какую технику анализ кадров вообще умеет находить. Своя модель — только свои классы (MOCS не знает
    кран-манипулятор и грузовик): требовать от неё такую технику — значит получать ложное «нет техники».
    Внешний сервис, push и демо-анализатор — любую."""
    analyzer = get_analyzer()
    if isinstance(analyzer, LocalAnalyzer):
        return frozenset(kind for kind in analyzer.detector.classes.values() if kind)
    return frozenset(EQUIPMENT_TYPES)


def provider_name() -> str:
    """Кто сейчас разбирает кадры: local, mock, http или push (auto — во что превратился)."""
    return "push" if get_settings().analysis_provider == "push" else get_analyzer().name
