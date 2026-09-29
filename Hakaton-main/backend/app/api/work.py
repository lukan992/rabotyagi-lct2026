"""Работы по камерам: что ответили сервисы аналитики по кадрам — рядом с графиком; «Определить сейчас»; справочник
видов работ для плана."""

import json
import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SiteIdQuery, get_site
from app.config import get_settings
from app.db import utcnow
from app.models import AnalyticsRequest, AnalyticsResult, Camera, Site, Snapshot, SpiderImport, Stage, User
from app.schemas import (
    AnalyticsCatalogOut,
    CameraWorkOut,
    CatalogWorkOut,
    ResourceAssessmentOut,
    ResourceEvidenceOut,
    ScheduleItemOut,
    ScheduleOut,
    ServiceAnswerOut,
    SiteWorkOut,
    TransitionOut,
    WorkEvidenceOut,
    WorkGroupOut,
    WorkRefOut,
)
from app.security import SITE_MANAGERS, CurrentUser, Session
from app.services.analytics.catalog import Catalog, CatalogError, object_type
from app.services.analytics.request import plan_problem, plan_works
from app.services.analytics.runner import fresh_snapshot, get_analytics, work_cameras
from app.services.engine import local_day
from app.services.spider import connection_fingerprint, resolve_connection

router = APIRouter(tags=["Работы по камерам"])
RECENT = 10  # сколько последних отправок камеры просматривать в поисках готового ответа
EVIDENCE = 6
_MISSING = object()


def _seconds(value: object) -> int | None:
    """overdue_seconds — число (у сервиса коллеги дробное: 857392.682) или null."""
    return round(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _moment(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


class _Names:
    """step_key ответа → наша работа (или вид работ справочника, если работу уже удалили из плана)."""

    def __init__(self, works: list[Stage], catalog: Catalog | None) -> None:
        self.works, self.catalog = {w.id: w for w in works}, catalog
        keys = sorted(map(re.escape, self.works), key=len, reverse=True)
        self._keys = re.compile(rf"(?<![\w-])({'|'.join(keys)})(?![\w-])") if keys else None

    def humanize(self, text: str) -> str:
        """Модель называет работы нашими id («котлована (s1-excavation)») — людям нужны названия из плана."""
        return self._keys.sub(lambda m: f"«{self.works[m.group(1)].name}»", text) if self._keys and text else text

    def ref(self, step_key: str, stage_id: int | None = None) -> WorkRefOut:
        work = self.works.get(step_key)
        stage_id = stage_id if stage_id is not None else (work.catalog_stage_id if work else None)
        if work:
            name = work.name
        elif self.catalog and stage_id in self.catalog.works:
            name = self.catalog.works[stage_id].name
        else:
            name = "работа удалена из плана"
        return WorkRefOut(step_key=step_key, stage_id=stage_id, name=name, in_plan=work is not None)


def _model(result: dict) -> str | None:
    versions = result.get("versions") or {}
    parts = [versions.get("service_version")]
    if versions.get("vision_model") or versions.get("llm_model"):
        parts.append(" → ".join(dict.fromkeys(v for v in (versions.get("vision_model"), versions.get("llm_model")) if v)))
    if versions.get("matrix_version"):
        parts.append(f"матрица {versions['matrix_version']}")
    text = ", ".join(p for p in parts if p)
    return text[:160] or None


def _request_metadata(request: AnalyticsRequest | None) -> dict[str, Any] | None:
    if request is None or not request.metadata_json:
        return None
    try:
        metadata = json.loads(request.metadata_json)
    except (TypeError, ValueError):
        return None
    return metadata if isinstance(metadata, dict) else None


def _spider_metadata(request: AnalyticsRequest | None) -> tuple[dict, bool] | None:
    metadata = _request_metadata(request)
    if metadata is None:
        return None
    source = metadata.get("source_context")
    if (
        metadata.get("schema_version") != "frame-analysis-input-v2"
        or not isinstance(source, dict)
        or source.get("source_system") != "spider"
        or not isinstance(source.get("source_snapshot_id"), str)
    ):
        return None
    return source, isinstance(metadata.get("resource_plan"), dict)

def _spider_limitations(request: AnalyticsRequest | None, stale: bool) -> list[str]:
    """Локальная provenance-подсказка, не часть и не модификация ответа vendor-сервиса."""
    limitations = []
    if request is not None and isinstance(request.notes, list) and "spider_context_unavailable" in request.notes:
        limitations.append("Spider был выбран, но успешного импорта нет; дополнительный контекст не передан.")
    context = _spider_metadata(request)
    if context is None:
        return limitations
    source, has_resource_plan = context
    image_origin = "на фотографии" if request and request.trigger == "photo" else "камеры"
    limitations.append(
        f"Spider: дополнительный план и ресурсы; это не наблюдения {image_origin}."
        if has_resource_plan
        else f"Spider: дополнительный контекст; это не наблюдения {image_origin}."
    )
    if source.get("data_type") == "synthetic_demo":
        limitations.append("Spider: источник synthetic_demo.")
    if stale:
        limitations.append("Spider: используется последний успешный импорт; источник устарел.")
    return limitations


def _pointer_tokens(pointer: str) -> list[str] | None:
    if not pointer.startswith("/"):
        return None
    tokens = []
    for part in pointer[1:].split("/"):
        decoded, index = [], 0
        while index < len(part):
            char = part[index]
            if char == "~":
                if index + 1 == len(part) or part[index + 1] not in "01":
                    return None
                decoded.append("~" if part[index + 1] == "0" else "/")
                index += 2
            else:
                decoded.append(char)
                index += 1
        tokens.append("".join(decoded))
    return tokens


def _pointer_value(document: dict[str, Any], tokens: list[str]) -> object:
    value: object = document
    for token in tokens:
        if isinstance(value, dict):
            if token not in value:
                return _MISSING
            value = value[token]
        elif isinstance(value, list):
            if not token.isascii() or not token.isdecimal() or (len(token) > 1 and token.startswith("0")):
                return _MISSING
            index = int(token)
            if index >= len(value):
                return _MISSING
            value = value[index]
        else:
            return _MISSING
    return value


def _safe_description_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split())
    if not text or re.search(r"(?:https?://|www\.|(?:token|secret|password|парол)[\s:=])", text, re.IGNORECASE):
        return fallback
    return text[:200]


def _safe_quantity(value: object) -> str | None:
    return str(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _resource_description(metadata: dict[str, Any], tokens: list[str]) -> str:
    if len(tokens) >= 3 and tokens[:2] == ["resource_plan", "stages"] and tokens[2].isdecimal():
        plan = metadata.get("resource_plan")
        stages = plan.get("stages") if isinstance(plan, dict) else None
        index = int(tokens[2])
        stage = stages[index] if isinstance(stages, list) and index < len(stages) and isinstance(stages[index], dict) else None
        if stage is not None:
            stage_name = _safe_description_text(stage.get("name"), "этап плана")
            if len(tokens) >= 5 and tokens[3] == "equipment" and tokens[4].isdecimal():
                equipment = stage.get("equipment")
                equipment_index = int(tokens[4])
                item = (
                    equipment[equipment_index]
                    if isinstance(equipment, list) and equipment_index < len(equipment) and isinstance(equipment[equipment_index], dict)
                    else None
                )
                if item is not None:
                    label = _safe_description_text(item.get("source_name"), "позиция плана")
                    quantity = _safe_quantity(item.get("planned_quantity"))
                    suffix = f", плановое количество: {quantity}" if quantity is not None else ""
                    return f"План: этап «{stage_name}»; позиция «{label}»{suffix}."
            return f"План: этап «{stage_name}»."
    if len(tokens) >= 3 and tokens[:2] == ["equipment_observation", "items"] and tokens[2].isdecimal():
        observation = metadata.get("equipment_observation")
        items = observation.get("items") if isinstance(observation, dict) else None
        index = int(tokens[2])
        item = items[index] if isinstance(items, list) and index < len(items) and isinstance(items[index], dict) else None
        if item is not None:
            quantity = _safe_quantity(item.get("count"))
            suffix = f", количество: {quantity}" if quantity is not None else ""
            class_code = item.get("class_code")
            if isinstance(class_code, str):
                return f"Локальное наблюдение: класс «{_safe_description_text(class_code, 'не указан')}»{suffix}."
            source_name = _safe_description_text(item.get("source_class_code"), "обозначение источника не указано")
            return f"Локальное наблюдение: обозначение источника «{source_name}»{suffix}."
    if len(tokens) >= 3 and tokens[:2] == ["cv", "detections"] and tokens[2].isdecimal():
        cv = metadata.get("cv")
        detections = cv.get("detections") if isinstance(cv, dict) else None
        index = int(tokens[2])
        detection = (
            detections[index]
            if isinstance(detections, list) and index < len(detections) and isinstance(detections[index], dict)
            else None
        )
        if detection is not None:
            class_code = _safe_description_text(detection.get("class_code"), "не указан")
            return f"Локальное наблюдение: класс «{class_code}», количество: 1."
    if tokens and tokens[0] == "resource_target":
        return "Выбор ресурсного этапа для этого кадра."
    if tokens and tokens[0] == "source_context":
        return "Контекст Spider для этого кадра."
    return "Исходные данные доказательства доступны."


def _resource_evidence(request: AnalyticsRequest, assessment: dict) -> list[ResourceEvidenceOut]:
    """Resolve only this request's stored evidence pointers; never expose the underlying source documents."""
    metadata = _request_metadata(request)
    pointers = [*assessment.get("evidence_refs", [])]
    for item in assessment.get("equipment_items", []):
        if isinstance(item, dict):
            pointers.extend(item.get("evidence_refs", []))
    evidence, seen = [], set()
    for pointer in pointers:
        if not isinstance(pointer, str) or pointer in seen:
            continue
        seen.add(pointer)
        tokens = _pointer_tokens(pointer)
        available = metadata is not None and tokens is not None and _pointer_value(metadata, tokens) is not _MISSING
        description = (
            _resource_description(metadata, tokens)
            if available and metadata is not None and tokens is not None
            else "Исходные данные доказательства недоступны"
        )
        evidence.append(ResourceEvidenceOut(pointer=pointer, description=description, available=available))
    return evidence


def _resource_assessment(result: dict, request: AnalyticsRequest | None) -> tuple[ResourceAssessmentOut, str] | None:
    metadata = _request_metadata(request)
    assessment = result.get("resource_assessment")
    mode = result.get("analysis_mode")
    if (
        metadata is None
        or metadata.get("schema_version") != "frame-analysis-input-v2"
        or result.get("schema_version") != "frame-analysis-result-v2"
        or mode not in {"demonstration", "operational"}
        or not isinstance(assessment, dict)
    ):
        return None
    try:
        return ResourceAssessmentOut.model_validate(assessment), mode
    except ValueError:
        return None


async def _spider_stale_at_request(session: AsyncSession, request: AnalyticsRequest | None) -> bool:
    """Восстановить состояние источника на момент подготовки конкретного v2-входа."""
    context = _spider_metadata(request)
    if context is None or request is None:
        return False
    source, _ = context
    imports = list(
        await session.scalars(
            select(SpiderImport)
            .where(SpiderImport.site_id == request.site_id, SpiderImport.started_at <= request.at)
            .order_by(SpiderImport.started_at.desc(), SpiderImport.id.desc())
        )
    )
    success = next(
        (
            item
            for item in imports
            if item.status == "succeeded" and item.snapshot_id == source["source_snapshot_id"]
        ),
        None,
    )
    if success is None:
        return False
    latest = next((item for item in imports if item.source_url == success.source_url), None)
    stale = bool(latest and latest.status == "failed" and latest.started_at > success.started_at)
    refresh_seconds = get_settings().camera_stage_monitor_refresh_seconds
    return stale or (
        refresh_seconds > 0
        and success.finished_at is not None
        and success.finished_at + timedelta(seconds=refresh_seconds) < request.at
    )


def _answer(
    service: str,
    request: AnalyticsRequest | None,
    row: AnalyticsResult | None,
    names: _Names,
    newer_pending: bool,
    spider_stale: bool,
) -> ServiceAnswerOut:
    local_limitations = _spider_limitations(request, spider_stale)
    source = _spider_metadata(request)
    answer = ServiceAnswerOut(
        service=service,
        state=row.state if row else "pending",
        at=row.finished_at if row else None,
        observed_at=request.observed_at if request else None,
        outcome=None,
        groups=[],
        transition=None,
        schedule=None,
        limitations=local_limitations,
        model=None,
        error_code=row.error_code if row else None,
        error=row.error if row else None,
        newer_pending=newer_pending,
        resource_assessment=None,
        analysis_mode=None,
        resource_evidence=[],
        spider_snapshot_id=source[0]["source_snapshot_id"] if source else None,
    )
    if row is None or row.state != "done" or not row.result:
        return answer
    result = row.result
    answer.outcome, answer.model = row.outcome, _model(result)
    vendor_limitations = [names.humanize(str(item))[:1000] for item in result.get("limitations") or []]
    answer.limitations = [*vendor_limitations[:20 - len(local_limitations)], *local_limitations]
    resource = _resource_assessment(result, request)
    if resource is not None:
        assessment, mode = resource
        answer.resource_assessment, answer.analysis_mode = assessment, mode
        answer.resource_evidence = _resource_evidence(request, assessment.model_dump())
    for group in result["current_work"]["work_groups"]:
        answer.groups.append(
            WorkGroupOut(
                match=str(group.get("match_status") or ""),
                works=[names.ref(c["step_key"], c["stage_id"]) for c in group["candidates"]],
                visual_state=str(group.get("visual_state") or ""),
                explanation=names.humanize(str(group.get("explanation") or "")),
                evidence=[
                    WorkEvidenceOut(
                        source=str(e.get("source")),
                        role=str(e.get("role")),
                        explanation=names.humanize(str(e.get("explanation") or "")),
                    )
                    for e in (group.get("evidence") or [])[:EVIDENCE]
                    if isinstance(e, dict)
                ],
                area=group.get("area_bbox") if isinstance(group.get("area_bbox"), list) else None,
            )
        )
    transition = result["transition"]
    if transition.get("status") != "not_evaluated":
        answer.transition = TransitionOut(
            status=transition["status"],
            current=names.ref(transition["current_step_key"]) if transition.get("current_step_key") else None,
            next=names.ref(transition["next_step_key"]) if transition.get("next_step_key") else None,
            first_at=_moment(transition.get("evidence_first_at")),
            last_at=_moment(transition.get("evidence_last_at")),
            points=int(transition.get("supporting_observations") or 0),
        )
    schedule = result["schedule"]
    if schedule.get("status") != "not_evaluated":
        answer.schedule = ScheduleOut(
            status=schedule["status"],
            items=[
                ScheduleItemOut(
                    work=names.ref(item["step_key"], item.get("stage_id")),
                    status=str(item.get("status")),
                    reason=str(item.get("reason_code")),
                    overdue_s=_seconds(item.get("overdue_seconds")),
                    evidence_at=_moment(item.get("evidence_at")),
                )
                for item in schedule.get("items") or []
                if isinstance(item, dict) and item.get("step_key")
            ],
        )
    return answer


async def _camera(session: AsyncSession, camera: Camera, services: list[str], names: _Names, planned: set[str]) -> CameraWorkOut:
    rows_by_service = {}
    for service in services:
        rows_by_service[service] = list(
            await session.execute(
                select(AnalyticsRequest, AnalyticsResult)
                .join(AnalyticsResult, AnalyticsResult.request_id == AnalyticsRequest.id)
                .where(AnalyticsRequest.camera_id == camera.id, AnalyticsResult.service == service)
                .order_by(AnalyticsRequest.at.desc(), AnalyticsRequest.id.desc())
                .limit(RECENT)
            )
        )
    answers, frames = [], []
    for service in services:
        newer_pending, shown = False, None
        rows = rows_by_service[service]
        for request, row in rows:  # от свежих к старым: ответ, который уже пришёл; более свежий, который ждём, — отметкой
            if row.state == "pending":
                newer_pending = True
                continue
            shown = (request, row)
            break
        if shown:
            request, row = shown
            answers.append(
                _answer(service, request, row, names, newer_pending, await _spider_stale_at_request(session, request))
            )
            frames.append(request)
        elif newer_pending:
            answers.append(_answer(service, rows[0][0], None, names, True, False))
    requests = {
        request.id: request
        for rows in rows_by_service.values()
        for request, _ in rows
    }
    latest = max(requests.values(), key=lambda request: (request.at, request.id), default=None)
    # A single camera image cannot represent answers computed from different frames.
    frame = frames[0] if frames else latest
    shared_frame = len({request.snapshot_id for request in frames}) <= 1
    snapshot = await session.get(Snapshot, frame.snapshot_id) if frame and frame.snapshot_id and shared_frame else None
    candidates = {w.step_key for a in answers if a.outcome == "assessed" for g in a.groups for w in g.works}
    assessed = any(a.outcome == "assessed" for a in answers)
    return CameraWorkOut(
        camera_id=camera.id,
        camera_name=camera.name,
        zone_name=camera.zone.name,
        sent_at=latest.at if latest else None,
        image_url=snapshot.image_url if snapshot else None,
        answers=answers,
        matches_plan=bool(candidates & planned) if assessed and planned else None,
    )


async def site_work(session: AsyncSession, site: Site, user: User) -> SiteWorkOut:
    """Технические причины (адреса сервисов, коды отказов) — руководителю и администратору; остальным — только суть."""
    analytics, settings = get_analytics(), get_settings()
    managers = user.role in SITE_MANAGERS
    services = list(analytics.clients)
    catalog = analytics.catalog.peek() if analytics.enabled else None
    works = await plan_works(session, site.id)
    today = local_day(utcnow())
    planned = [w for w in works if w.start_date <= today <= w.end_date]
    cameras = await work_cameras(session, site.id)
    names = _Names(works, catalog)
    blocks = [await _camera(session, camera, services, names, {w.id for w in planned}) for camera in cameras]
    if not managers:
        for answer in (a for block in blocks for a in block.answers):
            answer.error = None
    sent = [b.sent_at for b in blocks if b.sent_at]
    # камеру, по которой ещё не отправляли, расписание возьмёт в ближайшую минуту — срока не показываем
    next_at = min(sent) + timedelta(minutes=settings.analytics_interval_min) if sent and len(sent) == len(blocks) else None
    problem = analytics.problems.get(site.id)
    plan_issue = plan_problem(works, catalog, site.kind) if analytics.enabled else None
    connection = await resolve_connection(session, site.id)
    source_configured = (
        await session.scalar(
            select(SpiderImport.id)
            .where(
                SpiderImport.site_id == site.id,
                SpiderImport.source_url == connection.origin,
                SpiderImport.connection_fingerprint == connection_fingerprint(connection),
                SpiderImport.status == "succeeded",
            )
            .limit(1)
        )
        if connection.origin
        else None
    )
    return SiteWorkOut(
        source_configured=source_configured is not None,
        enabled=analytics.enabled,
        services=services,
        can_run=analytics.enabled and managers,
        running=analytics.running(site.id),
        llm_waiting=analytics.llm_waiting,
        planned=[w.name for w in planned],
        plan_issue=plan_issue if managers or not plan_issue else "план не сопоставлен со справочником сервисов",
        problem=problem[1] if problem and managers else None,
        cameras=blocks,
        catalog_version=catalog.version if catalog else None,
        next_at=next_at if next_at and next_at > utcnow() else None,  # просрочено — расписание ждёт свежих кадров
    )


@router.get(
    "/sites/{site_id}/work-analysis",
    response_model=SiteWorkOut,
    summary="Работы по камерам: последние ответы сервисов аналитики по кадрам камер рабочих зон",
)
async def get_site_work(site_id: str, user: CurrentUser, session: Session) -> SiteWorkOut:
    site = await get_site(session, user, site_id)
    return await site_work(session, site, user)


@router.post(
    "/sites/{site_id}/work-analysis",
    response_model=SiteWorkOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Отправить свежие кадры объекта сервисам аналитики сейчас (руководитель, администратор); ответ — в фоне",
)
async def run_site_work(site_id: str, user: CurrentUser, session: Session) -> SiteWorkOut:
    site = await get_site(session, user, site_id)
    if user.role not in SITE_MANAGERS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Отправлять кадры вне расписания могут руководитель и администратор")
    analytics = get_analytics()
    if not analytics.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Сервисы аналитики не подключены: задайте DETERMINISTIC_SERVICE_URL и/или VLM_LLM_SERVICE_URL",
        )
    cameras = await work_cameras(session, site.id)
    if not cameras:
        raise HTTPException(status.HTTP_409_CONFLICT, "На объекте нет камер рабочих зон — сервисам нечего отправлять")
    now = utcnow()
    if not [c for c in cameras if await fresh_snapshot(session, c.id, now)]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Нет свежих кадров с камер рабочих зон — отправлять нечего")
    analytics.start_site(site.id)  # уже идёт — просто покажем, что идёт
    return await site_work(session, site, user)


@router.get(
    "/analytics/catalog",
    response_model=AnalyticsCatalogOut,
    summary="Виды работ из справочника сервисов аналитики — для сопоставления с работами плана",
)
async def analytics_catalog(user: CurrentUser, session: Session, site_id: SiteIdQuery = None) -> AnalyticsCatalogOut:
    if user.role not in SITE_MANAGERS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Справочник нужен тем, кто ведёт план: руководителю и администратору")
    kind = object_type((await get_site(session, user, site_id)).kind) if site_id else None
    analytics = get_analytics()
    empty = AnalyticsCatalogOut(enabled=analytics.enabled, version=None, object_type=kind, works=[], error=None)
    if not analytics.enabled:
        return empty
    try:
        catalog = await analytics.catalog.get()
    except CatalogError as exc:
        return empty.model_copy(update={"error": f"справочник не получен: {exc}"})
    works = [
        CatalogWorkOut(
            stage_id=w.stage_id,
            name=w.name,
            path=list(w.path[:-1] if w.path and w.path[-1] == w.name else w.path),
            kind=w.kind,
        )
        for w in catalog.works_for(kind)
    ]
    error = f"сервисы не ответили, показан сохранённый справочник: {analytics.catalog.error}" if analytics.catalog.error else None
    return AnalyticsCatalogOut(enabled=True, version=catalog.version, object_type=kind, works=works, error=error)
