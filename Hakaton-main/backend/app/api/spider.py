"""Authenticated access to persisted Camera Stage Monitor source evidence."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import get_site
from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import SpiderConnection, SpiderImport, SpiderObservationAsset, SpiderSnapshot, User
from app.schemas import (
    SpiderConnectionIn,
    SpiderConnectionOut,
    SpiderImportOut,
    SpiderObservationAssetOut,
    SpiderOut,
    SpiderPrepareIn,
    SpiderSnapshotOut,
)
from app.security import SITE_MANAGERS, CurrentUser, Session, encrypt_secret, require_roles
from app.services.spider import (
    SpiderError,
    connection_fingerprint,
    import_source,
    prepare_observation,
    resolve_connection,
    validate_origin,
)

router = APIRouter(prefix="/sites/{site_id}/spider", tags=["Источник Camera Stage Monitor"])
settings = get_settings()

def _read_image(storage_path: str) -> bytes:
    path = Path(storage_path).resolve()
    root = (settings.data_dir / "spider" / "images").resolve()
    if not path.is_relative_to(root):
        raise FileNotFoundError
    return path.read_bytes()


def _document_envelope(snapshot: SpiderSnapshot, path: str) -> dict | None:
    document = snapshot.documents.get(path)
    if not isinstance(document, dict) or not isinstance(document.get("body"), str):
        return None
    try:
        decoded = json.loads(document["body"])
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _document_items(snapshot: SpiderSnapshot, path: str) -> list[dict]:
    decoded = _document_envelope(snapshot, path)
    items = decoded.get("items") if decoded else None
    return items if isinstance(items, list) and all(isinstance(item, dict) for item in items) else []


def _manual_annotations(snapshot: SpiderSnapshot) -> tuple[list[dict], str | None, bool | None]:
    document = _document_envelope(snapshot, "/api/v1/photo-equipment")
    if not document:
        return [], None, None
    items = document.get("items")
    annotations = items if isinstance(items, list) and all(isinstance(item, dict) for item in items) else []
    annotation_type = document.get("annotation_type")
    requires_validation = document.get("requires_validation")
    return (
        annotations,
        annotation_type if isinstance(annotation_type, str) else None,
        requires_validation if isinstance(requires_validation, bool) else None,
    )


def _import_out(row: SpiderImport) -> SpiderImportOut:
    return SpiderImportOut(
        id=row.id,
        status=row.status,
        snapshot_id=row.snapshot_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error_code=row.error_code,
        error_message=row.error_message,
    )


def _snapshot_out(snapshot: SpiderSnapshot) -> SpiderSnapshotOut:
    manual_annotations, manual_annotation_type, manual_requires_validation = _manual_annotations(snapshot)
    return SpiderSnapshotOut(
        id=snapshot.id,
        resource_revision_id=snapshot.resource_revision_id,
        source_url=snapshot.source_url,
        api_version=snapshot.api_version,
        data_source=snapshot.data_source,
        data_type=snapshot.data_type,
        warning=snapshot.warning,
        created_at=snapshot.created_at,
        resources=snapshot.resources,
        source_observations=_document_items(snapshot, "/api/v1/observations"),
        manual_annotation_type=manual_annotation_type,
        manual_requires_validation=manual_requires_validation,
        manual_annotations=manual_annotations,
        source_comparisons=_document_items(snapshot, "/api/v1/comparisons"),
    )


def _asset_out(asset: SpiderObservationAsset, snapshot: SpiderSnapshot, site_id: str) -> SpiderObservationAssetOut:
    return SpiderObservationAssetOut(
        id=asset.id,
        snapshot_id=asset.snapshot_id,
        observation_id=asset.observation_id,
        image_sha256=asset.image_sha256,
        media_type=asset.media_type,
        width=asset.width,
        height=asset.height,
        observed_at=asset.observed_at,
        timestamp_quality=asset.timestamp_quality,
        fetched_at=asset.fetched_at,
        image_url=f"/api/sites/{site_id}/spider/images/{asset.id}",
        target=asset.target_document,
        target_error=asset.target_error,
        data_type=snapshot.data_type,
        warning=snapshot.warning,
    )


def _source_error(error: SpiderError) -> HTTPException:
    if error.code == "source_not_configured":
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Источник Camera Stage Monitor не настроен")
    if error.code in {"source_snapshot_not_found", "source_observation_not_found"}:
        return HTTPException(status.HTTP_404_NOT_FOUND, "Снимок источника или наблюдение не найдено")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Источник Camera Stage Monitor вернул некорректные данные")

async def _connection_out(session: Session, site_id: str) -> SpiderConnectionOut:
    connection = await resolve_connection(session, site_id)
    return SpiderConnectionOut(
        url=connection.origin,
        has_token=bool(connection.token),
        configured=connection.origin is not None,
        custom=connection.custom,
    )


@router.get("/connection", response_model=SpiderConnectionOut, summary="Подключение источника для объекта")
async def spider_connection(site_id: str, user: CurrentUser, session: Session) -> SpiderConnectionOut:
    await get_site(session, user, site_id)
    return await _connection_out(session, site_id)


@router.put("/connection", response_model=SpiderConnectionOut, summary="Изменить подключение источника для объекта")
async def update_spider_connection(
    site_id: str,
    body: SpiderConnectionIn,
    user: Annotated[User, require_roles(*SITE_MANAGERS)],
    session: Session,
) -> SpiderConnectionOut:
    await get_site(session, user, site_id)
    try:
        origin = validate_origin(body.url)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    current = await session.get(SpiderConnection, site_id)
    token_supplied = "token" in body.model_fields_set
    if current is None:
        current = SpiderConnection(
            site_id=site_id,
            source_url=origin,
            token_enc=encrypt_secret(body.token) if token_supplied and body.token else None,
        )
        session.add(current)
    else:
        changed_origin = current.source_url != origin
        current.source_url = origin
        if token_supplied:
            current.token_enc = encrypt_secret(body.token) if body.token else None
        elif changed_origin:
            # A credential belongs to its original host and must never follow a URL change.
            current.token_enc = None
    await session.commit()
    return await _connection_out(session, site_id)



@router.post("/import", response_model=SpiderImportOut, summary="Загрузить пять документов источника")
async def import_spider(
    site_id: str,
    _: Annotated[User, require_roles(*SITE_MANAGERS)],
    session: Session,
) -> SpiderImportOut:
    await get_site(session, _, site_id)
    try:
        row = await import_source(SessionLocal, site_id)
    except SpiderError as exc:
        raise _source_error(exc) from None
    if row.status != "succeeded":
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Источник Camera Stage Monitor недоступен или вернул некорректные данные")
    return _import_out(row)


@router.get("", response_model=SpiderOut, summary="Последний успешный снимок источника для объекта")
async def spider_status(site_id: str, user: CurrentUser, session: Session) -> SpiderOut:
    await get_site(session, user, site_id)
    connection = await resolve_connection(session, site_id)
    query = select(SpiderImport).where(SpiderImport.site_id == site_id)
    if connection.origin is None:
        imports: list[SpiderImport] = []
    else:
        imports = list(await session.scalars(
            query.where(
                SpiderImport.source_url == connection.origin,
                SpiderImport.connection_fingerprint == connection_fingerprint(connection),
            ).order_by(SpiderImport.started_at.desc(), SpiderImport.id.desc())
        ))
    latest = imports[0] if imports else None
    success = next((item for item in imports if item.status == "succeeded" and item.snapshot_id), None)
    snapshot = await session.get(SpiderSnapshot, success.snapshot_id) if success and success.snapshot_id else None
    limitations = list(snapshot.resources.get("limitations", [])) if snapshot else ["source_not_imported"]
    if connection.origin is None:
        limitations.append("source_not_configured")
    stale = bool(latest and latest.status == "failed" and (success is None or latest.started_at > success.started_at))
    if success and settings.camera_stage_monitor_refresh_seconds > 0 and success.finished_at:
        stale = stale or success.finished_at + timedelta(seconds=settings.camera_stage_monitor_refresh_seconds) < utcnow()
    return SpiderOut(
        snapshot=_snapshot_out(snapshot) if snapshot else None,
        last_import=_import_out(latest) if latest else None,
        last_success_at=success.finished_at if success else None,
        stale=stale,
        limitations=limitations,
    )


@router.post("/observations/{observation_id}/prepare", response_model=SpiderObservationAssetOut, summary="Сохранить фото и проверить этап источника")
async def prepare_spider_observation(
    site_id: str,
    observation_id: str,
    body: SpiderPrepareIn,
    _: Annotated[User, require_roles(*SITE_MANAGERS)],
    session: Session,
) -> SpiderObservationAssetOut:
    await get_site(session, _, site_id)
    try:
        asset = await prepare_observation(SessionLocal, site_id, body.snapshot_id, observation_id)
    except SpiderError as exc:
        raise _source_error(exc) from None
    snapshot = await session.get(SpiderSnapshot, asset.snapshot_id)
    assert snapshot is not None
    return _asset_out(asset, snapshot, site_id)


@router.get("/images/{asset_id}", summary="Сохранённое аутентифицированное фото источника")
async def spider_image(site_id: str, asset_id: str, user: CurrentUser, session: Session) -> Response:
    await get_site(session, user, site_id)
    connection = await resolve_connection(session, site_id)
    if connection.origin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фото источника не найдено")
    asset = await session.scalar(
        select(SpiderObservationAsset)
        .join(SpiderImport, SpiderImport.snapshot_id == SpiderObservationAsset.snapshot_id)
        .where(
            SpiderObservationAsset.id == asset_id,
            SpiderObservationAsset.site_id == site_id,
            SpiderObservationAsset.connection_fingerprint == connection_fingerprint(connection),
            SpiderImport.site_id == site_id,
            SpiderImport.source_url == connection.origin,
            SpiderImport.connection_fingerprint == connection_fingerprint(connection),
            SpiderImport.status == "succeeded",
        )
        .limit(1)
    )
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фото источника не найдено")
    try:
        data = await asyncio.to_thread(_read_image, asset.storage_path)
    except OSError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фото источника не найдено") from None
    return Response(content=data, media_type=asset.media_type)
