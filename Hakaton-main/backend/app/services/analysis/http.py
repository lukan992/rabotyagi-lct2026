"""Клиент внешнего сервиса анализа кадров (будущая модель распознавания).

Контракт (его должен реализовать сервис анализа):

    POST {SK_ANALYSIS_API_URL}
    Content-Type: multipart/form-data
        image      — кадр JPEG 1280×720
        camera_id  — идентификатор камеры (необязательно)
        taken_at   — время кадра в ISO 8601 (необязательно)
    Authorization: Bearer {SK_ANALYSIS_API_KEY}   (если ключ задан)

    200 OK, application/json:
    {
      "model": "yolo11m-construction-v1",
      "detections": [
        {"type": "excavator", "confidence": 0.94, "box": {"x": 24.5, "y": 41.5, "w": 46.5, "h": 57.0}}
      ]
    }

    type — один из: excavator, dump_truck, roller, manipulator, mixer, bulldozer, truck, crane.
    box  — проценты от кадра (0..100), x и y — левый верхний угол.
"""

import time
from datetime import datetime

import httpx

from app.services.analysis.base import AnalysisError, AnalysisResult, DetectedObject, DetectionError, UnknownType, read_detection


class HttpAnalyzer:
    name = "http"

    def __init__(
        self, url: str, api_key: str | None, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.url = url
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(timeout=timeout_s, headers=headers, transport=transport)

    async def analyze(self, image: bytes, *, camera_id: str | None = None, taken_at: datetime | None = None) -> AnalysisResult:
        started = time.perf_counter()
        data = {k: v for k, v in {"camera_id": camera_id, "taken_at": taken_at and taken_at.isoformat()}.items() if v}
        try:
            response = await self._client.post(self.url, files={"image": ("frame.jpg", image, "image/jpeg")}, data=data)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AnalysisError(f"Сервис анализа недоступен: {exc}") from exc
        # ответ «[]», «null» или {"detections": null} раньше ронял фоновую проверку целиком
        if not isinstance(payload, dict) or not isinstance(payload.get("detections", []), list):
            raise AnalysisError(f"Непонятный ответ сервиса анализа: {str(payload)[:200]}")

        detections, skipped = [], set()
        for number, item in enumerate(payload.get("detections", []), 1):
            try:
                kind, confidence, x, y, w, h = read_detection(item)
            except UnknownType:
                skipped.add(str(item.get("type")))
                continue
            except DetectionError as exc:  # рамка в долях или пикселях, уверенность в процентах — говорим, что именно
                raise AnalysisError(f"Сервис анализа: объект № {number} — {exc}") from exc
            detections.append(DetectedObject(kind, round(confidence, 3), x, y, w, h))
        note = f"Пропущены неизвестные типы техники: {', '.join(sorted(skipped))}" if skipped else None
        model = payload.get("model")
        return AnalysisResult(
            provider=self.name,
            detections=detections,
            model=model[:80] if isinstance(model, str) else None,
            note=note,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
