"""Bounded adapter for Camera Stage Monitor (Spider).

The adapter preserves source documents verbatim in immutable snapshots.  It never
turns Spider's demonstration observations into local camera detections or v1
analytics history.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import secrets
import warnings
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import utcnow
from app.models import SpiderConnection, SpiderImport, SpiderObservationAsset, SpiderSnapshot, new_id
from app.security import decrypt_secret

JSON_LIMIT = 2_000_000
SOURCE_PATHS = (
    "/api/v1/stages",
    "/api/v1/equipment",
    "/api/v1/observations",
    "/api/v1/photo-equipment",
    "/api/v1/comparisons",
)
MEDIA_TYPES = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
_import_lock = asyncio.Lock()
_prepare_lock = asyncio.Lock()


class SpiderError(Exception):
    """Safe, stable reason for a source failure; never carries source body or token."""

    def __init__(self, code: str, message: str | None = None, *, http_status: int | None = None):
        self.code = code
        self.message = message or code
        self.http_status = http_status
        self.document: Any | None = None
        super().__init__(self.message)


@dataclass(frozen=True)
class SourceDocument:
    path: str
    text: str
    sha256: str
    data: dict[str, Any]
    fetched_at: datetime
    status: int

    def stored(self) -> dict[str, Any]:
        return {"body": self.text, "sha256": self.sha256}

    def fetch_record(self) -> dict[str, Any]:
        return {"path": self.path, "at": self.fetched_at.isoformat(), "http_status": self.status, "body_sha256": self.sha256}


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def validate_origin(value: str) -> str:
    """Return a canonical HTTP(S) origin, rejecting paths and credentials."""
    parsed = urlsplit(value.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("нужен origin http(s)://host[:port]") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("нужен origin http(s)://host[:port]")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return f"{parsed.scheme}://{host}{f':{port}' if port is not None else ''}"


@dataclass(frozen=True)
class SpiderConnectionConfig:
    origin: str | None
    token: str | None
    custom: bool


async def resolve_connection(session: AsyncSession, site_id: str) -> SpiderConnectionConfig:
    """Resolve an explicit site connection before the legacy global fallback."""
    connection = await session.get(SpiderConnection, site_id)
    if connection is not None:
        try:
            origin = validate_origin(connection.source_url)
        except ValueError:
            origin = None
        return SpiderConnectionConfig(origin=origin, token=decrypt_secret(connection.token_enc), custom=True)
    settings = get_settings()
    try:
        origin = validate_origin(settings.camera_stage_monitor_url) if settings.camera_stage_monitor_url else None
    except ValueError:
        origin = None
    token = settings.camera_stage_monitor_token.get_secret_value() if settings.camera_stage_monitor_token else None
    return SpiderConnectionConfig(origin=origin, token=token, custom=False)


def _origin(value: str | None) -> str:
    if not value:
        raise SpiderError("source_not_configured")
    try:
        return validate_origin(value)
    except ValueError:
        raise SpiderError("source_not_configured") from None


def _number(value: object, field: str, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise SpiderError("source_invalid_document", f"Некорректное число {field}")
    if integer and (not isinstance(value, int) or value < 0):
        raise SpiderError("source_invalid_document", f"Некорректное целое число {field}")
    return value


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise SpiderError("source_invalid_document", f"Некорректная дата {field}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SpiderError("source_invalid_document", f"Некорректная дата {field}") from None
    if parsed.tzinfo is None:
        raise SpiderError("source_invalid_document", f"Дата {field} должна содержать часовой пояс")
    return value


def _items(document: SourceDocument, *, expected: type = list) -> list[dict[str, Any]] | dict[str, Any]:
    data = document.data
    if data.get("api_version") != "1.0.0":
        raise SpiderError("source_version_unsupported")
    result = data.get("items")
    count = data.get("count")
    if not isinstance(result, expected) or (expected is list and (not isinstance(count, int) or count != len(result))):
        raise SpiderError("source_invalid_document")
    if expected is list and not all(isinstance(item, dict) for item in result):
        raise SpiderError("source_invalid_document")
    return result


def _resource_stage(item: dict[str, Any]) -> dict[str, Any]:
    code, name = item.get("code"), item.get("name")
    if not isinstance(code, str) or not code or not isinstance(name, str) or not name:
        raise SpiderError("source_invalid_document")
    start, finish = _timestamp(item.get("start"), f"{code}.start"), _timestamp(item.get("finish"), f"{code}.finish")
    if datetime.fromisoformat(finish) < datetime.fromisoformat(start):
        raise SpiderError("source_invalid_document")
    shifts = _number(item.get("planned_work_shifts"), f"{code}.planned_work_shifts", integer=True)
    volume = _number(item.get("planned_volume"), f"{code}.planned_volume")
    volume_unit = item.get("volume_unit")
    productivity = item.get("planned_productivity")
    equipment = item.get("equipment")
    if not isinstance(volume_unit, str) or not isinstance(productivity, dict) or not isinstance(equipment, list):
        raise SpiderError("source_invalid_document")
    productivity_value = _number(productivity.get("value"), f"{code}.planned_productivity.value")
    productivity_unit = productivity.get("unit")
    if not isinstance(productivity_unit, str):
        raise SpiderError("source_invalid_document")
    normalized_equipment = []
    for resource in equipment:
        if not isinstance(resource, dict) or not isinstance(resource.get("name"), str):
            raise SpiderError("source_invalid_document")
        normalized_equipment.append(
            {"source_name": resource["name"], "planned_quantity": _number(resource.get("quantity"), f"{code}.quantity", integer=True),
             "unit_productivity": None, "limitations": ["unit_productivity_not_provided"]}
        )
    return {
        "code": code, "name": name, "start": start, "finish": finish, "planned_work_shifts": shifts,
        "planned_volume": {"value": volume, "unit": volume_unit},
        "planned_productivity": {"value": productivity_value, "unit": productivity_unit}, "equipment": normalized_equipment,
    }


def _normalize(documents: dict[str, SourceDocument]) -> dict[str, Any]:
    markers = {
        (doc.data.get("api_version"), doc.data.get("data_source"), doc.data.get("data_type")) for doc in documents.values()
    }
    if len(markers) != 1 or any(not isinstance(value, str) for value in next(iter(markers))):
        raise SpiderError("source_invalid_document")
    stages_raw = _items(documents["/api/v1/stages"])
    equipment_raw = _items(documents["/api/v1/equipment"])
    observations = _items(documents["/api/v1/observations"])
    photos = _items(documents["/api/v1/photo-equipment"])
    comparisons = _items(documents["/api/v1/comparisons"])
    stages = [_resource_stage(item) for item in stages_raw]
    codes = {stage["code"] for stage in stages}
    if len(codes) != len(stages):
        raise SpiderError("source_invalid_document")
    by_code = {stage["code"]: stage for stage in stages}
    equipment_codes: set[str] = set()
    for item in equipment_raw:
        code = item.get("stage")
        if not isinstance(code, str) or code not in by_code or code in equipment_codes or item.get("stage_name") != by_code[code]["name"]:
            raise SpiderError("source_invalid_document")
        equipment_codes.add(code)
        compared = _resource_stage({**item, "code": code, "name": item["stage_name"], "start": by_code[code]["start"], "finish": by_code[code]["finish"]})
        stage = by_code[code]
        if {key: compared[key] for key in compared if key not in {"start", "finish"}} != {key: stage[key] for key in stage if key not in {"start", "finish"}}:
            raise SpiderError("source_resource_conflict")
    if equipment_codes != codes:
        raise SpiderError("source_resource_conflict")
    observation_ids = set()
    for item in observations:
        observation_id, stage = item.get("observation"), item.get("observed_stage")
        if not isinstance(observation_id, str) or observation_id in observation_ids or stage not in codes:
            raise SpiderError("source_invalid_document")
        observation_ids.add(observation_id)
        _timestamp(item.get("timestamp_iso"), f"{observation_id}.timestamp_iso")
        if not isinstance(item.get("image_url"), str):
            raise SpiderError("source_invalid_document")
    for items, id_field, stage_field in ((photos, "observation", "observed_stage_code"), (comparisons, "observation", "planned_stage")):
        for item in items:
            if item.get(id_field) not in observation_ids or item.get(stage_field) not in codes:
                raise SpiderError("source_invalid_document")
    return {
        "stages": stages,
        "limitations": ["stage_mapping_not_configured", "equipment_mapping_not_configured", "unit_productivity_not_provided"],
    }


class SpiderClient:
    """A single bounded HTTP client. All paths stay on the configured origin."""

    def __init__(
        self, origin: str, token: str | None, *, transport: httpx.AsyncBaseTransport | None = None
    ):
        settings = get_settings()
        self.origin = _origin(origin)
        self.timeout = settings.camera_stage_monitor_timeout_seconds
        self.retries = settings.camera_stage_monitor_max_retries
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.AsyncClient(transport=transport, timeout=self.timeout, follow_redirects=False, headers=headers)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _url(self, path: str) -> str:
        parsed = urlsplit(path)
        if not path.startswith("/") or parsed.scheme or parsed.netloc or parsed.fragment or parsed.username or parsed.password:
            raise SpiderError("unsafe_image_url")
        return f"{self.origin}{path}"

    async def _request(self, path: str, *, params: dict[str, str] | None = None, limit: int) -> tuple[bytes, int]:
        url = self._url(path)
        attempts = self.retries + 1
        for number in range(attempts):
            try:
                async with self.client.stream("GET", url, params=params) as response:
                    if 300 <= response.status_code < 400:
                        raise SpiderError("source_redirect", http_status=response.status_code)
                    if response.status_code >= 500:
                        if number + 1 == attempts:
                            raise SpiderError(f"source_http_{response.status_code}", http_status=response.status_code)
                        await asyncio.sleep(0.5)
                        continue
                    if response.status_code >= 400:
                        code = "source_unauthorized" if response.status_code in {401, 403} else f"source_http_{response.status_code}"
                        raise SpiderError(code, http_status=response.status_code)
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise SpiderError("source_response_too_large", http_status=response.status_code)
                        chunks.append(chunk)
                    return b"".join(chunks), response.status_code
            except SpiderError:
                raise
            except httpx.HTTPError as exc:
                if number + 1 == attempts:
                    raise SpiderError("source_network_error") from exc
                await asyncio.sleep(0.5)
        raise AssertionError("unreachable")

    async def document(self, path: str, *, params: dict[str, str] | None = None) -> SourceDocument:
        data, status = await self._request(path, params=params, limit=JSON_LIMIT)
        final_path = path if not params else f"{path}?timestamp={params['timestamp']}"
        try:
            text = data.decode("utf-8")
            decoded = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            error = SpiderError("source_invalid_json", http_status=status)
            error.document = SourceDocument(final_path, data.decode("utf-8", errors="replace"), hashlib.sha256(data).hexdigest(), {}, utcnow(), status)
            raise error from None
        if not isinstance(decoded, dict):
            error = SpiderError("source_invalid_json", http_status=status)
            error.document = SourceDocument(final_path, text, hashlib.sha256(data).hexdigest(), {}, utcnow(), status)
            raise error
        return SourceDocument(final_path, text, hashlib.sha256(data).hexdigest(), decoded, utcnow(), status)

    async def image(self, path: str) -> tuple[bytes, int]:
        return await self._request(path, limit=get_settings().max_frame_bytes)


def _image(data: bytes) -> tuple[str, int, int, str, str]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                media = MEDIA_TYPES.get(image.format or "")
                if media is None or getattr(image, "n_frames", 1) != 1:
                    raise SpiderError("source_invalid_image")
                image.load()
                width, height = image.size
    except SpiderError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise SpiderError("source_invalid_image") from None
    media_type, extension = media
    return media_type, width, height, hashlib.sha256(data).hexdigest(), extension


async def _failed_import(session_factory: async_sessionmaker[AsyncSession], site_id: str, origin: str, started_at: datetime, documents: dict[str, SourceDocument], error: SpiderError) -> SpiderImport:
    async with session_factory() as session:
        row = SpiderImport(id=new_id("spimp"), site_id=site_id, source_url=origin, status="failed", started_at=started_at, finished_at=utcnow(),
                           fetches=[doc.fetch_record() for doc in documents.values()], partial_documents={path: doc.stored() for path, doc in documents.items()},
                           error_code=error.code, error_message=error.message)
        session.add(row)
        await session.commit()
        return row


async def import_source(
    session_factory: async_sessionmaker[AsyncSession], site_id: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> SpiderImport:
    """Fetch the five source documents, validate as a set, then atomically publish an immutable snapshot."""
    async with session_factory() as session:
        connection = await resolve_connection(session, site_id)
    if connection.origin is None:
        raise SpiderError("source_not_configured")
    started_at = utcnow()
    client = SpiderClient(connection.origin, connection.token, transport=transport)
    documents: dict[str, SourceDocument] = {}
    try:
        async with _import_lock:
            for path in SOURCE_PATHS:
                document = await client.document(path)
                documents[path] = document
            resources = _normalize(documents)
            snapshot_id = hashlib.sha256(
                _canonical({"origin": client.origin, "documents": {path: doc.sha256 for path, doc in documents.items()}})
            ).hexdigest()
            revision = hashlib.sha256(_canonical({"resources": resources, "normalization_version": "1"})).hexdigest()
            async with session_factory() as session:
                snapshot = await session.get(SpiderSnapshot, snapshot_id)
                if snapshot is None:
                    first = documents[SOURCE_PATHS[0]].data
                    snapshot = SpiderSnapshot(
                        id=snapshot_id,
                        source_url=client.origin,
                        api_version=first["api_version"],
                        data_source=str(first.get("data_source", "")),
                        data_type=str(first.get("data_type", "")),
                        warning=str(first.get("warning", "")),
                        documents={path: doc.stored() for path, doc in documents.items()},
                        resources=resources,
                        normalization_version="1",
                        resource_revision_id=revision,
                        created_at=utcnow(),
                    )
                    session.add(snapshot)
                    # Models intentionally have no ORM relationship; flush the
                    # immutable parent before its import FK on SQLite as well.
                    await session.flush()
                row = SpiderImport(
                    id=new_id("spimp"),
                    site_id=site_id,
                    source_url=client.origin,
                    status="succeeded",
                    started_at=started_at,
                    finished_at=utcnow(),
                    snapshot_id=snapshot_id,
                    fetches=[doc.fetch_record() for doc in documents.values()],
                    partial_documents={},
                )
                session.add(row)
                await session.commit()
                return row
    except SpiderError as exc:
        if exc.document is not None:
            documents[exc.document.path] = exc.document
        return await _failed_import(session_factory, site_id, client.origin, started_at, documents, exc)
    finally:
        await client.aclose()


async def _refresh_group(
    session_factory: async_sessionmaker[AsyncSession], connection: SpiderConnectionConfig, site_ids: list[str]
) -> list[SpiderImport]:
    assert connection.origin is not None
    started_at = utcnow()
    client = SpiderClient(connection.origin, connection.token)
    documents: dict[str, SourceDocument] = {}
    try:
        async with _import_lock:
            for path in SOURCE_PATHS:
                documents[path] = await client.document(path)
            resources = _normalize(documents)
            snapshot_id = hashlib.sha256(
                _canonical({"origin": client.origin, "documents": {path: doc.sha256 for path, doc in documents.items()}})
            ).hexdigest()
            revision = hashlib.sha256(_canonical({"resources": resources, "normalization_version": "1"})).hexdigest()
            async with session_factory() as session:
                snapshot = await session.get(SpiderSnapshot, snapshot_id)
                if snapshot is None:
                    first = documents[SOURCE_PATHS[0]].data
                    snapshot = SpiderSnapshot(
                        id=snapshot_id,
                        source_url=client.origin,
                        api_version=first["api_version"],
                        data_source=str(first.get("data_source", "")),
                        data_type=str(first.get("data_type", "")),
                        warning=str(first.get("warning", "")),
                        documents={path: doc.stored() for path, doc in documents.items()},
                        resources=resources,
                        normalization_version="1",
                        resource_revision_id=revision,
                        created_at=utcnow(),
                    )
                    session.add(snapshot)
                    await session.flush()
                rows = [
                    SpiderImport(
                        id=new_id("spimp"),
                        site_id=site_id,
                        source_url=client.origin,
                        status="succeeded",
                        started_at=started_at,
                        finished_at=utcnow(),
                        snapshot_id=snapshot_id,
                        fetches=[doc.fetch_record() for doc in documents.values()],
                        partial_documents={},
                    )
                    for site_id in site_ids
                ]
                session.add_all(rows)
                await session.commit()
                return rows
    except SpiderError as exc:
        return [
            await _failed_import(session_factory, site_id, client.origin, started_at, documents, exc)
            for site_id in site_ids
        ]
    finally:
        await client.aclose()


async def refresh_sources(session_factory: async_sessionmaker[AsyncSession], site_ids: list[str]) -> list[SpiderImport]:
    """Refresh sites by equal active origin and credential, never across connections."""
    if not site_ids:
        return []
    groups: dict[tuple[str, str | None], tuple[SpiderConnectionConfig, list[str]]] = {}
    async with session_factory() as session:
        for site_id in dict.fromkeys(site_ids):
            connection = await resolve_connection(session, site_id)
            if connection.origin is None:
                continue
            key = (connection.origin, connection.token)
            if key not in groups:
                groups[key] = (connection, [])
            groups[key][1].append(site_id)
    imported: list[SpiderImport] = []
    for connection, grouped_site_ids in groups.values():
        imported.extend(await _refresh_group(session_factory, connection, grouped_site_ids))
    return imported


def _observations(snapshot: SpiderSnapshot) -> list[dict[str, Any]]:
    document = snapshot.documents.get("/api/v1/observations")
    if not isinstance(document, dict) or not isinstance(document.get("body"), str):
        return []
    try:
        decoded = json.loads(document["body"])
    except ValueError:
        return []
    return decoded.get("items", []) if isinstance(decoded, dict) and isinstance(decoded.get("items"), list) else []


def _verified_target(snapshot: SpiderSnapshot, observation: dict[str, Any], document: SourceDocument) -> dict[str, Any]:
    """Accept plan-at only when it describes this immutable source revision."""
    target = _items(document, expected=dict)
    if (
        document.data.get("data_source") != snapshot.data_source
        or document.data.get("data_type") != snapshot.data_type
        or datetime.fromisoformat(_timestamp(target.get("timestamp"), "plan-at.timestamp"))
        != datetime.fromisoformat(_timestamp(observation.get("timestamp_iso"), "observation.timestamp_iso"))
    ):
        raise SpiderError("source_revision_conflict")
    planned = target.get("planned_stage")
    if not isinstance(planned, dict):
        raise SpiderError("source_revision_conflict")
    planned_resource = _resource_stage(planned)
    snapshot_resource = next(
        (stage for stage in snapshot.resources.get("stages", []) if stage.get("code") == planned_resource["code"]), None
    )
    if planned_resource != snapshot_resource:
        raise SpiderError("source_revision_conflict")
    return planned


def _publish_image(directory: Path, path: Path, data: bytes, digest: str) -> None:
    """Atomically publish content-addressed bytes without replacing an existing asset."""
    directory.mkdir(parents=True, exist_ok=True)

    def verify() -> None:
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise SpiderError("source_storage_error") from exc
        if actual != digest:
            raise SpiderError("source_storage_conflict")

    if path.exists():
        verify()
        return
    temporary = directory / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            verify()
    except SpiderError:
        raise
    except OSError as exc:
        raise SpiderError("source_storage_error") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


async def prepare_observation(
    session_factory: async_sessionmaker[AsyncSession],
    site_id: str,
    snapshot_id: str,
    observation_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SpiderObservationAsset:
    """Prepare one observation serially so retries retain every target attempt."""
    async with _prepare_lock:
        return await _prepare_observation(
            session_factory, site_id, snapshot_id, observation_id, transport=transport
        )


async def _prepare_observation(
    session_factory: async_sessionmaker[AsyncSession], site_id: str, snapshot_id: str, observation_id: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> SpiderObservationAsset:
    """Persist one verified source photo and its explicitly requested source plan target."""
    async with session_factory() as session:
        connection = await resolve_connection(session, site_id)
        if connection.origin is None:
            raise SpiderError("source_snapshot_not_found")
        allowed = await session.scalar(
            select(SpiderImport.id)
            .where(
                SpiderImport.site_id == site_id,
                SpiderImport.source_url == connection.origin,
                SpiderImport.status == "succeeded",
                SpiderImport.snapshot_id == snapshot_id,
            )
            .limit(1)
        )
        snapshot = await session.get(SpiderSnapshot, snapshot_id)
        if allowed is None or snapshot is None:
            raise SpiderError("source_snapshot_not_found")
        observation = next((item for item in _observations(snapshot) if item.get("observation") == observation_id), None)
        if not isinstance(observation, dict):
            raise SpiderError("source_observation_not_found")
        existing = await session.scalar(select(SpiderObservationAsset).where(SpiderObservationAsset.snapshot_id == snapshot_id, SpiderObservationAsset.observation_id == observation_id).order_by(SpiderObservationAsset.fetched_at.desc()).limit(1))
        if existing and existing.target_document is not None:
            return existing
    client = SpiderClient(connection.origin, connection.token, transport=transport)
    try:
        if existing is None:
            image_url = observation.get("image_url")
            if not isinstance(image_url, str):
                raise SpiderError("unsafe_image_url")
            raw, _ = await client.image(image_url)
            media_type, width, height, digest, extension = _image(raw)
            directory = get_settings().data_dir / "spider" / "images"
            path = directory / f"{digest}.{extension}"
            await asyncio.to_thread(_publish_image, directory, path, raw, digest)
            asset = SpiderObservationAsset(id=new_id("spasset"), snapshot_id=snapshot_id, observation_id=observation_id, image_sha256=digest,
                                           media_type=media_type, width=width, height=height, storage_path=str(path), source_image_url=image_url,
                                           observed_at=datetime.fromisoformat(_timestamp(observation.get("timestamp_iso"), "timestamp_iso")),
                                           timestamp_quality="synthetic_demo", fetched_at=utcnow(), target_attempts=[])
            async with session_factory() as session:
                session.add(asset)
                await session.commit()
            existing = asset
        try:
            target_doc = await client.document("/api/v1/plan-at", params={"timestamp": observation["timestamp_iso"]})
            target = _verified_target(snapshot, observation, target_doc)
            attempt = {"at": utcnow().isoformat(), "http_status": target_doc.status, "body": target_doc.text, "body_sha256": target_doc.sha256}
            target_error = None
        except SpiderError as exc:
            target, target_error = None, exc.code
            attempt = {"at": utcnow().isoformat(), "http_status": exc.http_status, "error_code": exc.code}
        async with session_factory() as session:
            asset = await session.get(SpiderObservationAsset, existing.id)
            assert asset is not None
            attempts = list(asset.target_attempts or [])
            attempts.append(attempt)
            asset.target_attempts = attempts
            asset.target_document, asset.target_error = target, target_error
            await session.commit()
            return asset
    finally:
        await client.aclose()
