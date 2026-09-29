"""Запрос сервисам аналитики: один кадр одной камеры + metadata.

Базовый запрос содержит только кадр, CV, локальный план и историю. Перед отправкой
каждому сервису он становится самостоятельным wire-входом: VLM получает v2 с
сохранённым плановым контекстом Spider, остальные сервисы остаются на v1.

Что уходит из нашей базы:
 • frame — снимок камеры: image_id — id снимка, байты кадра — отдельной частью запроса;
 • cv — техника от нашей модели: наши типы → коды классов справочника, рамки → доли кадра [x0, y0, x1, y1];
 • scope — участок. План объекта у нас одной цепочкой, поэтому участок на объект один («main»), весь кадр
   (roi_bbox = null). Отправляются только камеры рабочих зон: на въезде и складе техника подъезжает, а не работает;
 • plan — работы плана (уровень 2) с видом работ по справочнику. Версия плана — отпечаток содержимого: любая правка
   даёт новую. Хоть одна работа без вида по справочнику — план не отправляется (plan = null), причина хранится у нас;
 • history — журнал наблюдений камер участка за analytics_history_days дней (без картинок) и отметки выполнения
   руководителя: 0 % — не начата, 1–99 % — идёт, 100 % — завершена. Точных дат начала и окончания у нас нет —
   actual_* всегда null.
"""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Camera, Observation, Site, Snapshot, SpiderImport, SpiderSnapshot, Stage
from app.schemas import SpiderResourcesOut
from app.services.analytics.catalog import Catalog, object_type
from app.services.analytics.images import FrameImage, load_image
from app.services.analytics.observations import detections_of
from app.services.spider import connection_fingerprint, resolve_connection

INPUT_SCHEMA = "frame-analysis-input-v1"
SPIDER_INPUT_SCHEMA = "frame-analysis-input-v2"
STREAM = "main"
MAX_DETECTIONS = 200
MAX_STEPS = 500
MAX_OBSERVATIONS = 500
MAX_EVENTS = 1000
MAX_METADATA_BYTES = 1_000_000


class RequestProblem(Exception):
    """Запрос собрать нельзя: объяснение для администратора."""


@dataclass
class BuiltRequest:
    request_id: str
    metadata: dict
    body: bytes  # metadata ровно в том виде, как уходит
    image: FrameImage
    input_sha256: str
    plan_note: str | None  # почему план не отправлен
    notes: list[str] = field(default_factory=list)  # что ещё не попало в запрос

    @property
    def plan_revision_id(self) -> str | None:
        return self.metadata["plan"]["revision_id"] if self.metadata["plan"] else None


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def moment(value: datetime) -> str:
    """RFC 3339 с поясом объекта: 2026-09-26T12:00:00.000+03:00."""
    return value.astimezone(_tz()).isoformat(timespec="milliseconds")


def day_start(day: date) -> str:
    return datetime.combine(day, time.min, _tz()).isoformat(timespec="seconds")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def serialize(metadata: dict) -> bytes:
    return json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def input_fingerprint(body: bytes, image: bytes) -> str:
    """input_sha256 (раздел 11): metadata как отправлена + байт 0x00 + байты кадра."""
    return hashlib.sha256(body + b"\x00" + image).hexdigest()


def bbox(x: float, y: float, w: float, h: float) -> list[float] | None:
    """Рамка в процентах (левый верхний угол, ширина, высота) → [x_min, y_min, x_max, y_max] в долях кадра."""
    x0, y0 = round(max(x / 100, 0.0), 6), round(max(y / 100, 0.0), 6)
    x1, y1 = round(min((x + w) / 100, 1.0), 6), round(min((y + h) / 100, 1.0), 6)
    return [x0, y0, x1, y1] if x0 < x1 and y0 < y1 else None


def cv_block(*, analyzed: bool, provider: str | None, model: str | None, detections: list[dict], catalog: Catalog,
             notes: list[str]) -> dict:  # fmt: skip
    """cv кадра (раздел 5). Анализ не удался — failed без рамок (демо-анализатор кадра не знает — unavailable)."""
    source = f"stroykontrol:analysis/{provider or 'unknown'}"
    if not analyzed:
        status = "unavailable" if provider == "mock" else "failed"
        return {"status": status, "source_ref": source, "model_version": model, "detections": []}
    items, skipped = [], set()
    for d in detections:
        code = catalog.class_code(d["type"])
        box = bbox(*d["box"])
        if code is None:
            skipped.add(d["type"])
        elif box is not None:
            confidence = d.get("confidence")
            items.append(
                {
                    "detection_id": f"d{d['id']}",
                    "class_code": code,
                    "bbox": box,
                    "confidence": None if confidence is None else round(min(max(float(confidence), 0.0), 1.0), 4),
                }
            )
    for kind in sorted(skipped):
        note = f"техника «{kind}» не отправлена: её класса нет в справочнике (задайте SK_ANALYTICS_CLASSES)"
        if note not in notes:
            notes.append(note)
    if len(items) > MAX_DETECTIONS:  # обрезать молча нельзя
        raise RequestProblem(f"на кадре {len(items)} рамок, а сервис принимает не больше {MAX_DETECTIONS}")
    return {"status": "ok", "source_ref": source, "model_version": model, "detections": items}


async def plan_works(session: AsyncSession, site_id: str) -> list[Stage]:
    """Работы плана (уровень 2) в порядке графика."""
    rows = await session.scalars(select(Stage).where(Stage.site_id == site_id, Stage.level == 2))
    return sorted(rows, key=lambda s: (s.start_date, s.position))


def plan_problem(works: list[Stage], catalog: Catalog | None, kind: str | None) -> str | None:
    """Почему план нельзя отправить сервисам; None — можно. Для запроса и для подсказки в интерфейсе."""
    if not works:
        return "в плане объекта нет работ"
    missing = [w.name for w in works if w.catalog_stage_id is None]
    if missing:
        return f"не выбран вид работ по справочнику: {', '.join(f'«{n}»' for n in missing[:5])}" + (
            " и др." if len(missing) > 5 else ""
        )
    if catalog is None:
        return None
    outdated = [w.name for w in works if w.catalog_version != catalog.version]
    if outdated:
        return (
            f"справочник сервисов обновился до версии {catalog.version} — проверьте вид работ у "
            + ", ".join(f"«{n}»" for n in outdated[:5])
            + (" и др." if len(outdated) > 5 else "")
        )
    for w in works:
        if problem := catalog.step_problem(w.catalog_stage_id, object_type(kind or "")):
            return f"«{w.name}»: {problem}"
    if len(works) > MAX_STEPS:
        return f"в плане {len(works)} работ, а сервис принимает не больше {MAX_STEPS}"
    return None


def plan_block(site: Site, works: list[Stage]) -> dict:
    steps = [
        {
            "step_key": w.id,
            "sequence_no": n,
            "stage_id": w.catalog_stage_id,
            "planned_start_at": day_start(w.start_date),
            "planned_end_at": day_start(w.end_date + timedelta(days=1)),  # дата окончания у нас включительно
        }
        for n, w in enumerate(works, start=1)
    ]
    plan_id = f"plan-{site.id}"
    return {
        "plan_id": plan_id,
        "revision_id": "rev-" + digest({"plan_id": plan_id, "stream": STREAM, "steps": steps})[:32],
        "source_ref": f"stroykontrol:sites/{site.id}/plan",
        "steps": steps,
    }


def progress_events(works: list[Stage], revision_id: str, as_of: datetime) -> list[dict]:
    """Отметки выполнения руководителя на момент кадра — подтверждённый ход работ (раздел 7)."""
    events = []
    for w in works:
        if w.fact_updated_at is None or w.fact_updated_at > as_of:
            continue
        state = "completed" if w.fact_progress >= 100 else "in_progress" if w.fact_progress > 0 else "not_started"
        at = moment(w.fact_updated_at)
        events.append(
            {
                "event_id": f"fact-{w.id}-{w.fact_updated_at.astimezone(_tz()):%Y%m%dT%H%M%S%f}",
                "step_key": w.id,
                "stage_id": w.catalog_stage_id,
                "plan_revision_id": revision_id,
                "state": state,
                "effective_at": at,
                "recorded_at": at,
                "source_ref": f"stroykontrol:stages/{w.id}/fact",
                "actual_started_at": None,
                "actual_completed_at": None,
            }
        )
    if len(events) > MAX_EVENTS:
        raise RequestProblem(f"отметок выполнения {len(events)}, а сервис принимает не больше {MAX_EVENTS}")
    return events


async def history_block(session: AsyncSession, *, site: Site, snapshot: Snapshot, catalog: Catalog, plan: dict | None,
                        works: list[Stage], notes: list[str]) -> dict:  # fmt: skip
    """Журнал наблюдений камер рабочих зон объекта за окно (без текущего кадра) и отметки выполнения.

    В запросе не больше 500 наблюдений. Если за окно их больше — окно укорачивается до последних 500 целиком
    (complete остаётся true: в укороченном окне переданы все записи), а не прореживается молча.
    """
    as_of = snapshot.taken_at
    window_start = as_of - timedelta(days=get_settings().analytics_history_days)
    rows = list(
        await session.scalars(
            select(Observation)
            .where(
                Observation.site_id == site.id,
                Observation.zone_kind == "work",
                Observation.observed_at >= window_start,
                Observation.observed_at <= as_of,
                Observation.image_id != snapshot.id,
            )
            .order_by(Observation.observed_at.desc(), Observation.id.desc())
            .limit(MAX_OBSERVATIONS + 1)
        )
    )
    if len(rows) > MAX_OBSERVATIONS:
        boundary = rows[MAX_OBSERVATIONS].observed_at
        rows = [r for r in rows[:MAX_OBSERVATIONS] if r.observed_at > boundary]
        window_start = rows[-1].observed_at if rows else as_of
        notes.append(f"история укорочена до {len(rows)} наблюдений: больше сервис не принимает")
    observations = [
        {
            "observation_id": f"obs-{r.id}",
            "image_id": r.image_id,
            "camera_id": r.camera_id,
            "observed_at": moment(r.observed_at),
            "source_ref": f"stroykontrol:snapshots/{r.image_id}",
            "image_sha256": r.image_sha256,
            "cv": cv_block(
                analyzed=r.analyzed, provider=r.provider, model=r.model, detections=r.detections, catalog=catalog, notes=notes
            ),
        }
        for r in reversed(rows)
    ]
    events = progress_events(works, plan["revision_id"], as_of) if plan else []
    body = {
        "as_of": moment(as_of),
        "window_start": moment(window_start),
        "complete": True,
        "observations": observations,
        "progress_events": events,
    }
    return {"snapshot_id": "hist-" + digest(body)[:32], **body}


async def spider_context(session: AsyncSession, site_id: str, *, now: datetime) -> dict | None:
    """Получить последний успешный нормализованный снимок Spider для v2-проекции.

    Это локальная запись происхождения, не wire-контракт: ``for_service`` преобразует
    её в закрытые vendor-поля и не переносит документы, URL, наблюдения или сравнения.
    """
    connection = await resolve_connection(session, site_id)
    if connection.origin is None:
        return None
    imports = list(
        await session.scalars(
            select(SpiderImport)
            .where(
                SpiderImport.site_id == site_id,
                SpiderImport.source_url == connection.origin,
                SpiderImport.connection_fingerprint == connection_fingerprint(connection),
            )
            .order_by(SpiderImport.started_at.desc(), SpiderImport.id.desc())
        )
    )
    success = next((row for row in imports if row.status == "succeeded" and row.snapshot_id), None)
    if success is None:
        return None
    snapshot = await session.get(SpiderSnapshot, success.snapshot_id)
    if snapshot is None or success.finished_at is None or snapshot.normalization_version != "1":
        raise RequestProblem("Контекст Spider не подготовлен: некорректный сохранённый импорт.")
    try:
        resources = SpiderResourcesOut.model_validate(snapshot.resources).model_dump(mode="json", by_alias=False)
    except ValidationError as exc:
        raise RequestProblem("Контекст Spider не подготовлен: некорректный сохранённый импорт.") from exc
    latest = imports[0]
    stale = bool(latest.status == "failed" and latest.started_at > success.started_at)
    refresh_seconds = get_settings().camera_stage_monitor_refresh_seconds
    if refresh_seconds > 0:
        stale = stale or success.finished_at + timedelta(seconds=refresh_seconds) < now
    return {
        "snapshot_id": snapshot.id,
        "resource_revision_id": snapshot.resource_revision_id,
        "data_type": snapshot.data_type,
        "stale": stale,
        "resources": resources,
    }


def _resource_problem(message: str) -> RequestProblem:
    return RequestProblem(f"Контекст Spider не подготовлен: {message}.")


def _source_type(value: object) -> tuple[str, str, str]:
    """Только заявленный synthetic_demo становится демонстрацией; качество остального не выдумываем."""
    if value == "synthetic_demo":
        return "synthetic_demo", "demonstration", "demonstration"
    if value == "production":
        return "production", "source_declared", "operational"
    return "unknown", "unknown", "operational"


def _spider_ref(snapshot_id: str, revision_id: str, suffix: str = "") -> str:
    """Неподписанный локальный URI снимка: идентифицирует неизменяемые данные без URL/токенов источника."""
    return f"spider:snapshots/{snapshot_id}/resource-revisions/{revision_id}{suffix}"


def _measure(value: object, *, field: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    number, unit = value.get("value"), value.get("unit")
    if number is None and unit is None:
        return None
    if (
        isinstance(number, bool)
        or not isinstance(number, int | float)
        or not math.isfinite(number)
        or not 0 <= number <= 10**15
        or isinstance(unit, bool)
        or not isinstance(unit, str)
        or not unit.strip()
        or len(unit) > 100
    ):
        raise _resource_problem(f"{field} нельзя передать без потери данных")
    return {"value": number, "unit": unit}


def _quantity(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or int(value) != value
        or not 0 <= value <= 1_000_000
    ):
        raise _resource_problem(f"{field} должно быть целым количеством")
    return int(value)

def _contract_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _resource_problem(f"{field} превышает или не соответствует ограничению v2")
    return value


def _contract_time(value: object, *, field: str) -> tuple[str, datetime]:
    text = _contract_text(value, field=field, maximum=40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _resource_problem(f"{field} не соответствует времени v2") from exc
    if parsed.tzinfo is None:
        raise _resource_problem(f"{field} должно содержать часовой пояс")
    return parsed.isoformat(timespec="milliseconds"), parsed


def _resource_plan(spider: dict) -> dict | None:
    """Проецировать нормализованные этапы строго как unmapped: соответствий с локальным планом нет."""
    snapshot_id = _contract_text(spider["snapshot_id"], field="ID снимка Spider", maximum=100)
    revision_id = _contract_text(spider["resource_revision_id"], field="ID ревизии Spider", maximum=100)
    stages = spider["resources"]["stages"]
    if not isinstance(stages, list) or len(stages) > 500:
        raise _resource_problem("число ресурсных этапов не соответствует ограничению v2")
    if not stages:
        return None
    parsed_stages = []
    seen_codes: set[str] = set()
    equipment_total = 0
    for stage in stages:
        code = _contract_text(stage["code"], field="код ресурсного этапа", maximum=100)
        name = _contract_text(stage["name"], field=f"название этапа {code}", maximum=1000)
        start, start_at = _contract_time(stage["start"], field=f"начало этапа {code}")
        finish, finish_at = _contract_time(stage["finish"], field=f"окончание этапа {code}")
        if finish_at <= start_at:
            raise _resource_problem("даты этапов несовместимы с контрактом ресурса")
        if code in seen_codes:
            raise _resource_problem("коды ресурсных этапов должны быть уникальны")
        seen_codes.add(code)
        equipment_items = stage["equipment"]
        if not isinstance(equipment_items, list) or len(equipment_items) > 200:
            raise _resource_problem(f"число строк техники этапа {code} не соответствует ограничению v2")
        equipment_total += len(equipment_items)
        if equipment_total > 10_000:
            raise _resource_problem("общее число строк техники не соответствует ограничению v2")
        parsed_stages.append((start_at, code, name, start, finish, stage))
    projected = []
    for sequence_no, (_, code, name, start, finish, stage) in enumerate(
        sorted(parsed_stages, key=lambda item: (item[0], item[1])), start=1
    ):
        shifts = stage["planned_work_shifts"]
        if shifts == 0:
            raise _resource_problem("нулевое количество плановых смен несовместимо с контрактом ресурса")
        if (
            shifts is not None
            and (isinstance(shifts, bool) or not isinstance(shifts, int | float) or not math.isfinite(shifts)
                 or not 0 < shifts <= 1_000_000)
        ):
            raise _resource_problem("количество плановых смен некорректно")
        stage_ref = _contract_text(
            _spider_ref(snapshot_id, revision_id, f"/stages/{code}"), field=f"source_ref этапа {code}", maximum=1000
        )
        equipment = []
        for index, item in enumerate(stage["equipment"]):
            source_name = _contract_text(item["source_name"], field=f"название техники этапа {code}", maximum=1000)
            source_ref = _contract_text(
                f"{stage_ref}/equipment/{index}", field=f"source_ref техники этапа {code}", maximum=1000
            )
            equipment.append(
                {
                    "item_id": "rp-" + digest([snapshot_id, revision_id, code, index])[:32],
                    "source_name": source_name,
                    "class_code": None,
                    "mapping_status": "unmapped",
                    "planned_quantity": _quantity(
                        item["planned_quantity"], field=f"плановое количество техники этапа {code}"
                    ),
                    "source_ref": source_ref,
                }
            )
        projected.append(
            {
                "stage_code": code,
                "name": name,
                "sequence_no": sequence_no,
                "planned_start_at": start,
                "planned_end_at": finish,
                "source_ref": stage_ref,
                "mapping_status": "unmapped",
                "mapped_step_keys": [],
                "planned_work_shifts": shifts,
                "planned_volume": _measure(stage["planned_volume"], field=f"плановый объём этапа {code}"),
                "planned_productivity": _measure(
                    stage["planned_productivity"], field=f"плановая производительность этапа {code}"
                ),
                "equipment": equipment,
            }
        )
    plan_ref = _contract_text(_spider_ref(snapshot_id, revision_id), field="source_ref ресурсного плана", maximum=1000)
    return {
        "plan_id": snapshot_id,
        "revision_id": revision_id,
        "source_ref": plan_ref,
        # Spider stages are not proven to describe our local plan stream.
        "plan_stream_code": None,
        "stages": projected,
    }


def _spider_v2(base: BuiltRequest, spider: dict) -> dict:
    data_type, timestamp_quality, analysis_mode = _source_type(spider["data_type"])
    cv, frame = base.metadata["cv"], base.metadata["frame"]
    observation = {
        "basis": "cv_detections" if cv["status"] == "ok" else "unavailable",
        "source_ref": cv["source_ref"] if cv["status"] == "ok" else None,
        "observed_at": frame["observed_at"],
        "coverage": "unknown",
        "requires_validation": False,
        "items": [],
    }
    return {
        "analysis_mode": analysis_mode,
        "source_context": {
            "source_system": "spider",
            "source_snapshot_id": spider["snapshot_id"],
            "source_ref": _spider_ref(spider["snapshot_id"], spider["resource_revision_id"]),
            "data_type": data_type,
            "timestamp_quality": timestamp_quality,
            # A completed local import does not prove whether Spider is live-synchronised.
            "spider_live_sync": None,
        },
        "resource_plan": _resource_plan(spider),
        "resource_target": None,
        "equipment_observation": observation,
    }


def for_service(base: BuiltRequest, *, request_id: str, service: str, spider: dict | None) -> BuiltRequest:
    """Сформировать неизменяемый для конкретного сервиса wire-вход из общего кадра."""
    metadata = {**base.metadata, "request_id": request_id}
    if spider is not None:
        metadata.update({"schema_version": SPIDER_INPUT_SCHEMA, **_spider_v2(base, spider)})
    body = serialize(metadata)
    if len(body) > MAX_METADATA_BYTES:
        raise RequestProblem(f"metadata {len(body)} байт, а сервис принимает не больше {MAX_METADATA_BYTES}")
    return BuiltRequest(
        request_id=request_id,
        metadata=metadata,
        body=body,
        image=base.image,
        input_sha256=input_fingerprint(body, base.image.data),
        plan_note=base.plan_note,
        notes=base.notes,
    )




async def build_request(session: AsyncSession, *, request_id: str, site: Site, camera: Camera | None, snapshot: Snapshot,
                        catalog: Catalog) -> BuiltRequest:  # fmt: skip
    """Собрать запрос по снимку камеры или самостоятельной загрузке."""
    image = load_image(snapshot.image_url)
    notes: list[str] = []
    works = await plan_works(session, site.id)
    plan_note = plan_problem(works, catalog, site.kind)
    plan = None if plan_note else plan_block(site, works)
    frame_camera_id = camera.id if camera is not None else f"photo_{snapshot.id}"
    upload = camera is None
    metadata = {
        "schema_version": INPUT_SCHEMA,
        "request_id": request_id,
        "site_id": site.id,
        "object_type_code": object_type(site.kind),
        "catalog_version": catalog.version,
        "frame": {
            "image_id": snapshot.id,
            "camera_id": frame_camera_id,
            "observed_at": moment(snapshot.taken_at),
            "source_ref": (
                f"stroykontrol:photo-analyses/{snapshot.id}"
                if upload
                else f"stroykontrol:snapshots/{snapshot.id}"
            ),
            "image_sha256": image.sha256,
            "media_type": image.media_type,
            "width": image.width,
            "height": image.height,
        },
        "scope": {
            "plan_stream_code": STREAM,
            "roi_bbox": None,
            "source_ref": (
                f"stroykontrol:photo-analyses/{snapshot.id}"
                if upload
                else f"stroykontrol:cameras/{camera.id}/zones/{camera.zone_id}"
            ),
        },
        "cv": cv_block(
            analyzed=snapshot.analyzed,
            provider=snapshot.provider,
            model=snapshot.model,
            detections=detections_of(snapshot),
            catalog=catalog,
            notes=notes,
        ),
        "plan": plan,
        "history": await history_block(
            session, site=site, snapshot=snapshot, catalog=catalog, plan=plan, works=works, notes=notes
        ),
    }
    body = serialize(metadata)
    if len(body) > MAX_METADATA_BYTES:
        raise RequestProblem(f"metadata {len(body)} байт, а сервис принимает не больше {MAX_METADATA_BYTES}")
    return BuiltRequest(
        request_id=request_id,
        metadata=metadata,
        body=body,
        image=image,
        input_sha256=input_fingerprint(body, image.data),
        plan_note=plan_note,
        notes=notes,
    )
