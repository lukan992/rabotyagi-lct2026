"""Приём готовых детекций от внешнего сервиса анализа (push-режим).

Если сервис анализа сам забирает видео (например, из шлюза), он присылает сюда кадр и найденную технику.
Кадр попадает туда же, куда и результаты собственного конвейера, — в ближайшую сверку объекта (раз в минуту).
Чтобы сервер при этом не разбирал кадры сам, задайте SK_ANALYSIS_PROVIDER=push.
Ключ — SK_INGEST_API_KEY или ключ сервиса разметки SK_TRACKER_API_KEY: сервису удобнее один ключ на всё.
"""

import hmac
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status
from PIL import Image

from app.config import get_settings
from app.db import utcnow
from app.models import Camera
from app.schemas import EquipmentEventAck, EquipmentEventIn, IngestOut
from app.security import Session
from app.services.analysis import AnalysisResult, DetectedObject
from app.services.analysis.base import DetectionError, UnknownType, pad_box, read_detection
from app.services.camera_client import CameraError, normalize_frame_async
from app.services.pipeline import CameraLive, LiveFrame, get_pipeline
from app.services.equipment_visits import EquipmentVisitError, ingest_equipment_event

settings = get_settings()
router = APIRouter(prefix="/ingest", tags=["Приём данных от сервиса анализа"])


log = logging.getLogger("stroykontrol.ingest")
_warned_mode = False


def _image_size(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):  # тот же список, что у normalize_frame
        return None  # непонятную картинку отклонит normalize_frame ниже, с понятным текстом


def _key_ok(given: str | None) -> bool:
    keys = [k for k in (settings.ingest_api_key, settings.tracker_api_key) if k]
    return bool(given) and any(hmac.compare_digest(given.encode(), k.encode()) for k in keys)  # байты: не-ASCII не роняет



@router.post(
    "/equipment-events",
    response_model=EquipmentEventAck,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Принять lifecycle-событие tracker и обновить журнал наблюдения",
)
async def ingest_equipment_event_endpoint(
    payload: EquipmentEventIn,
    session: Session,
    x_api_key: Annotated[str | None, Header()] = None,
) -> EquipmentEventAck:
    if not settings.ingest_api_key and not settings.tracker_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Приём выключен: задайте SK_INGEST_API_KEY или SK_TRACKER_API_KEY"
        )
    if not _key_ok(x_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный ключ")
    try:
        return await ingest_equipment_event(session, payload)
    except EquipmentVisitError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None

@router.post("/snapshots", response_model=IngestOut, status_code=status.HTTP_202_ACCEPTED, summary="Принять кадр с детекциями")
async def ingest_snapshot(
    session: Session,
    camera_id: Annotated[str, Form()],
    detections: Annotated[
        str, Form(description='JSON: [{"type":"excavator","confidence":0.94,"box":{"x":..,"y":..,"w":..,"h":..}}]')
    ],
    image: Annotated[UploadFile, File()],
    taken_at: Annotated[datetime | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> IngestOut:
    if not settings.ingest_api_key and not settings.tracker_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Приём выключен: задайте SK_INGEST_API_KEY или SK_TRACKER_API_KEY"
        )
    if not _key_ok(x_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный ключ")
    camera = await session.get(Camera, camera_id)
    if camera is None or camera.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Камера не найдена")
    try:
        raw = json.loads(detections)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "detections — не JSON") from None
    if not isinstance(raw, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "detections должен быть JSON-массивом объектов")
    items, skipped = [], set()
    data = await image.read(settings.max_frame_bytes + 1)
    size = _image_size(data)  # рамки посчитаны по исходному кадру, а храним его дополненным до 16:9
    for number, item in enumerate(raw, 1):
        try:
            kind, confidence, x, y, w, h = read_detection(item)
            items.append(DetectedObject(kind, confidence, *(pad_box(x, y, w, h, *size) if size else (x, y, w, h))))
        except UnknownType:
            skipped.add(str(item.get("type")))
        except DetectionError as exc:  # номер объекта и что не так — разработчику сервиса не придётся гадать
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Объект № {number} в detections: {exc}") from None
    try:
        jpeg = await normalize_frame_async(data)
    except CameraError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None

    now = utcnow()
    at = taken_at or now
    if at.tzinfo is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "taken_at должен содержать часовой пояс")
    if at > now + timedelta(minutes=1):  # местное время, присланное как UTC, сдвигало бы картину на часы вперёд
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Время кадра в будущем — проверьте часовой пояс")

    live = get_pipeline().live.setdefault(camera.id, CameraLive(camera.id))
    if live.frame and at <= live.frame.at:  # опоздавший кадр не должен менять текущую картину
        return IngestOut(accepted=False, detections=len(items), note="Кадр старее уже полученного с этой камеры — пропущен")
    provider = (model or "external")[:40]  # столбец provider — 40 символов
    frame = LiveFrame(at=at, jpeg=jpeg, result=AnalysisResult(provider=provider, detections=items, model=model))
    live.frame, live.online, live.error, live.received_at = frame, True, None, at
    live.history.append(frame)
    notes = ["Кадр принят: попадёт в ближайшую сверку объекта."]
    if skipped:
        notes.append(f"Пропущены не наши типы: {', '.join(sorted(skipped))}.")
    if settings.analysis_provider != "push":
        # сервер и сам разбирает кадры этой камеры — картина смешивается: то его разбор, то сервиса
        notes.append(f"Сервер сам разбирает кадры (SK_ANALYSIS_PROVIDER={settings.analysis_provider}) — включите push.")
        global _warned_mode
        if not _warned_mode:
            _warned_mode = True
            log.warning(
                "Кадры приходят от сервиса анализа, но SK_ANALYSIS_PROVIDER=%s — задайте push", settings.analysis_provider
            )
    return IngestOut(accepted=True, detections=len(items), note=" ".join(notes))
