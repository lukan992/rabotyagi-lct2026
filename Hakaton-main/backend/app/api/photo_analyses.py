"""Authenticated first-class still-photo analytics, independent from live cameras."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import get_site
from app.db import utcnow
from app.models import AnalyticsRequest, AnalyticsResult, Detection, Snapshot, new_id
from app.schemas import ApiModel, ServiceAnswerOut
from app.security import SITE_MANAGERS, CurrentUser, Session, require_roles
from app.services.analysis import AnalysisError, AnalysisResult, get_photo_analyzer
from app.services.analytics.catalog import Catalog
from app.services.analytics.images import MAX_BYTES, ImageProblem, load_image, photo_path, prepare_image
from app.services.analytics.request import plan_works
from app.services.analytics.runner import get_analytics
from app.services.camera_client import CameraError, normalize_frame_async

router = APIRouter(prefix="/sites/{site_id}/photo-analyses", tags=["Анализ загруженных фото"])
managers = [require_roles(*SITE_MANAGERS)]


class PhotoAnalysisOut(ApiModel):
    id: str
    image_url: str
    at: datetime
    llm_waiting: int
    answers: list[ServiceAnswerOut]


def _store_photo(path, data: bytes) -> None:
    """Create one private upload without following or replacing a pre-existing path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def _remove_photo(path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass



def _image_url(site_id: str, photo_id: str) -> str:
    return f"/api/sites/{site_id}/photo-analyses/{photo_id}/image"


def _form_bool(value: object) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0", ""}:
            return False
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "useSpider должен быть true или false")


async def _upload(request: Request) -> tuple[bytes, bool]:
    try:
        form = await request.form()
    except Exception as exc:  # malformed multipart must be a client error, not an app failure
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Нужен один файл изображения") from exc
    items = list(form.multi_items())
    image_values = [value for key, value in items if key == "image"]
    spider_values = [value for key, value in items if key == "useSpider"]
    if (
        len(image_values) != 1
        or len(spider_values) > 1
        or any(key not in {"image", "useSpider"} for key, _ in items)
        or not hasattr(image_values[0], "read")
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Нужен ровно один файл image и необязательный useSpider")
    raw = await image_values[0].read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Файл слишком большой")
    try:
        await asyncio.to_thread(prepare_image, raw)  # validate untrusted input before decoding/resizing it again
        normalized = await normalize_frame_async(raw, force_reencode=True)
        image = await asyncio.to_thread(prepare_image, normalized)
    except (ImageProblem, CameraError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return image.data, _form_bool(spider_values[0]) if spider_values else False


async def _answers(session: Session, snapshot: Snapshot) -> list[ServiceAnswerOut]:
    """Render photo requests with the established work-answer serializer, not a second response format."""
    from app.api.work import _answer, _Names, _spider_stale_at_request

    requests = list(
        await session.scalars(
            select(AnalyticsRequest)
            .where(AnalyticsRequest.snapshot_id == snapshot.id, AnalyticsRequest.trigger == "photo")
            .order_by(AnalyticsRequest.at.desc(), AnalyticsRequest.id.desc())
        )
    )
    latest: dict[str, tuple[AnalyticsRequest, AnalyticsResult]] = {}
    for item in requests:
        for result in item.results:
            latest.setdefault(result.service, (item, result))
    analytics = get_analytics()
    catalog: Catalog | None = analytics.catalog.peek()
    names = _Names(await plan_works(session, snapshot.site_id), catalog)
    answer_rows = []
    services = dict.fromkeys([*analytics.clients, *latest])
    for service in services:
        item = latest.get(service)
        request, result = item if item else (None, None)
        answer_rows.append(
            _answer(
                service,
                request,
                result,
                names,
                newer_pending=False,
                spider_stale=await _spider_stale_at_request(session, request),
            )
        )
    return answer_rows
async def _out(session: Session, snapshot: Snapshot) -> PhotoAnalysisOut:
    return PhotoAnalysisOut(
        id=snapshot.id,
        image_url=_image_url(snapshot.site_id, snapshot.id),
        at=snapshot.taken_at,
        llm_waiting=get_analytics().llm_waiting,
        answers=await _answers(session, snapshot),
    )

@router.post("", response_model=PhotoAnalysisOut, status_code=status.HTTP_201_CREATED, dependencies=managers)
async def create_photo_analysis(site_id: str, request: Request, user: CurrentUser, session: Session) -> PhotoAnalysisOut:
    site = await get_site(session, user, site_id)
    analytics = get_analytics()
    if not analytics.clients:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Сервисы аналитики не подключены")
    data, use_spider = await _upload(request)
    photo_id = new_id("photo")
    path = photo_path(photo_id)
    assert path is not None  # generated IDs have the only accepted shape
    at = utcnow()
    analyzer = get_photo_analyzer()
    try:
        result = await analyzer.analyze(data, camera_id=f"photo_{photo_id}", taken_at=at)
    except AnalysisError as exc:
        result = AnalysisResult(provider=analyzer.name, supported=False, note=str(exc))
    try:
        await asyncio.to_thread(_store_photo, path, data)
        snapshot = Snapshot(
            id=photo_id,
            camera_id=None,
            site_id=site.id,
            taken_at=at,
            image_url=f"photo:{photo_id}",
            source="photo",
            photo_use_spider=use_spider,
            analyzed=result.supported,
            provider=result.provider,
            model=result.model,
            analysis_ms=result.elapsed_ms,
            note=result.note,
        )
        for detection in result.detections:
            snapshot.detections.append(
                Detection(
                    equipment_type=detection.type,
                    confidence=detection.confidence,
                    x=detection.x,
                    y=detection.y,
                    w=detection.w,
                    h=detection.h,
                    moving=None,
                )
            )
        session.add(snapshot)
        await session.commit()
    except OSError as exc:
        await asyncio.to_thread(_remove_photo, path)
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE, "Не удалось сохранить загруженное фото") from exc
    except Exception:
        await asyncio.to_thread(_remove_photo, path)
        await session.rollback()
        raise
    await session.refresh(snapshot)
    built = await analytics.prepare_photo(snapshot.id)
    if built:
        asyncio.create_task(analytics.complete(built), name=f"photo-analysis-{snapshot.id}")
    return await _out(session, snapshot)


async def _photo(session: Session, user: CurrentUser, site_id: str, photo_id: str) -> Snapshot:
    await get_site(session, user, site_id)
    snapshot = await session.get(Snapshot, photo_id)
    if snapshot is None or snapshot.site_id != site_id or snapshot.source != "photo" or snapshot.camera_id is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Загруженное фото не найдено")
    return snapshot


@router.get("", response_model=list[PhotoAnalysisOut])
async def list_photo_analyses(site_id: str, user: CurrentUser, session: Session) -> list[PhotoAnalysisOut]:
    await get_site(session, user, site_id)
    photos = list(
        await session.scalars(
            select(Snapshot)
            .where(Snapshot.site_id == site_id, Snapshot.source == "photo", Snapshot.camera_id.is_(None))
            .order_by(Snapshot.taken_at.desc(), Snapshot.id.desc())
            .limit(100)
        )
    )
    return [await _out(session, photo) for photo in photos]


@router.get("/{photo_id}", response_model=PhotoAnalysisOut)
async def get_photo_analysis(site_id: str, photo_id: str, user: CurrentUser, session: Session) -> PhotoAnalysisOut:
    return await _out(session, await _photo(session, user, site_id, photo_id))


@router.get("/{photo_id}/image")
async def photo_image(site_id: str, photo_id: str, user: CurrentUser, session: Session) -> Response:
    snapshot = await _photo(session, user, site_id, photo_id)
    try:
        image = await asyncio.to_thread(load_image, snapshot.image_url)
    except ImageProblem:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Загруженное фото не найдено") from None
    return Response(content=image.data, media_type=image.media_type, headers={"Cache-Control": "private, no-store"})
