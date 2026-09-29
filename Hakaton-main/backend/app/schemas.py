"""Схемы API. JSON — в camelCase и совпадает с типами фронтенда (frontend/src/data/types.ts)."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.config import get_settings
from app.equipment import EQUIPMENT_TYPES
from app.models import (
    OPEN_STATUSES,
    Alert,
    Camera,
    CheckRun,
    EquipmentEvent,
    EquipmentVisit,
    Rule,
    Site,
    Snapshot,
    Stage,
    User,
    Zone,
)
from app.services.camera_client import CameraAddress

EquipmentType = Literal["excavator", "dump_truck", "roller", "manipulator", "mixer", "bulldozer", "truck", "crane"]
RoleId = Literal["foreman", "manager", "inspector", "admin"]
assert set(EquipmentType.__args__) == set(EQUIPMENT_TYPES)
_TZ = ZoneInfo(get_settings().timezone)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---------- доступ ----------
class LoginIn(ApiModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class DemoLoginIn(ApiModel):
    role: RoleId


class UserOut(ApiModel):
    id: str
    login: str
    name: str
    role: RoleId
    phone: str
    site_ids: list[str]
    is_active: bool = True


class TokenOut(ApiModel):
    token: str
    user: UserOut


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id, login=u.login, name=u.name, role=u.role, phone=u.phone, site_ids=[s.id for s in u.sites], is_active=u.is_active
    )


class UserIn(ApiModel):
    login: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=120)
    role: RoleId
    phone: str = Field(default="", max_length=32)
    site_ids: list[str] = []
    password: str = Field(min_length=6, max_length=200)


class UserPatch(ApiModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    role: RoleId | None = None
    phone: str | None = Field(default=None, max_length=32)
    site_ids: list[str] | None = None
    is_active: bool | None = None


class PasswordIn(ApiModel):
    password: str = Field(min_length=6, max_length=200)


# ---------- объекты ----------
# вид объекта — как models.SITE_KINDS: виды «Справочника видов работ» (жильё, школа, детский сад…, дороги) и ещё
# соцобъект без уточнения, промышленный, другое
SiteKind = Literal[
    "housing",
    "education",
    "preschool",
    "healthcare",
    "sports",
    "culture",
    "administrative",
    "office",
    "roads",
    "public",
    "industrial",
    "other",
]


class SiteOut(ApiModel):
    id: str
    name: str
    address: str
    contractor: str
    foreman: str
    kind: SiteKind
    work_from: int  # рабочее время: с этого часа (местного)
    work_to: int  # до этого часа, не включая; 0–24 — круглосуточно
    work_days: str  # пн…вс: «1111110» — с понедельника по субботу
    current_stage_id: str | None
    plan_progress: int | None  # сколько должно быть сделано по графику, % — по всему плану; None — плана нет
    fact_progress: int | None  # сколько сделано по факту, %


def site_out(s: Site, current_stage_id: str | None, progress: tuple[int, int] | None) -> SiteOut:
    return SiteOut(
        id=s.id,
        name=s.name,
        address=s.address,
        contractor=s.contractor,
        foreman=s.foreman_name,
        kind=s.kind,
        work_from=s.work_from,
        work_to=s.work_to,
        work_days=s.work_days,
        current_stage_id=current_stage_id,
        plan_progress=progress[0] if progress else None,
        fact_progress=progress[1] if progress else None,
    )


class SiteIn(ApiModel):
    name: str = Field(min_length=2, max_length=200)
    address: str = Field(default="", max_length=200)
    contractor: str = Field(default="", max_length=200)
    kind: SiteKind = "other"
    # рабочее время; поля нет — не менять (у нового объекта — круглосуточно)
    work_from: int = Field(default=0, ge=0, le=23)
    work_to: int = Field(default=24, ge=1, le=24)
    work_days: str = Field(default="1111111", pattern=r"^[01]{7}$")
    foreman_id: str | None = None  # прораб объекта: получит к нему доступ; явный null — снять прораба, поля нет — не трогать


class ZoneIn(ApiModel):
    name: str = Field(min_length=2, max_length=200)
    kind: Literal["work", "gate", "storage"] = "work"


class StageIn(ApiModel):
    name: str = Field(min_length=2, max_length=200)
    level: Literal[1, 2] = 2  # 1 — укрупнённый этап, 2 — работы с правилом «этап → техника»
    parent_id: str | None = None
    start: date
    end: date
    rule_key: str | None = None
    fact_progress: int | None = Field(default=None, ge=0, le=100)  # None — не менять
    # вид работ по справочнику сервисов аналитики (только у работ); явный null — снять, поля нет — не трогать
    catalog_stage_id: int | None = Field(default=None, ge=0)


class StageProgressIn(ApiModel):
    fact_progress: int = Field(ge=0, le=100)


class ZoneOut(ApiModel):
    id: str
    site_id: str
    name: str
    kind: Literal["work", "gate", "storage"]


def zone_out(z: Zone) -> ZoneOut:
    return ZoneOut(id=z.id, site_id=z.site_id, name=z.name, kind=z.kind)


class PlanImportWork(ApiModel):
    line: int  # номер строки в файле
    name: str
    start: date | None
    end: date | None
    rule_key: str | None
    catalog_stage_id: int | None
    errors: list[str]  # из-за них план не загрузится
    notes: list[str]  # пояснения: правило подобрано по названию, работа без правила…


class PlanImportPhase(ApiModel):
    line: int
    name: str
    start: date | None  # явные даты этапа или от начала первой его работы до конца последней
    end: date | None
    errors: list[str]
    works: list[PlanImportWork]


class PlanImportOut(ApiModel):
    file_name: str
    phases: list[PlanImportPhase]
    works: int
    errors: int  # сколько ошибок во всём файле: пока они есть, план не загружается
    existing: int  # этапов и работ уже в плане объекта — при замене они удалятся
    applied: bool  # false — только предпросмотр


class StageOut(ApiModel):
    id: str
    site_id: str
    parent_id: str | None
    level: int
    name: str
    start: date
    end: date
    status: Literal["done", "in_progress", "planned"]
    rule_key: str | None
    plan_progress: int  # сколько должно быть сделано к сегодняшнему дню по графику, %
    fact_progress: int  # сколько сделано по факту, %
    fact_updated_at: datetime | None
    catalog_stage_id: int | None  # вид работ по справочнику сервисов аналитики
    catalog_version: str | None  # по какой версии справочника он выбран


def stage_out(s: Stage, today: date) -> StageOut:
    return StageOut(
        id=s.id,
        site_id=s.site_id,
        parent_id=s.parent_id,
        level=s.level,
        name=s.name,
        start=s.start_date,
        end=s.end_date,
        status=s.status_on(today),
        rule_key=s.rule_key,
        plan_progress=s.plan_progress_on(today),
        fact_progress=s.fact_progress,
        fact_updated_at=s.fact_updated_at,
        catalog_stage_id=s.catalog_stage_id,
        catalog_version=s.catalog_version,
    )


# ---------- правила ----------
class RuleRequirement(ApiModel):
    type: EquipmentType
    min: int = Field(ge=1, le=50)
    why: str = ""
    risk: str = ""


class RuleUnexpected(ApiModel):
    type: EquipmentType
    why: str = ""
    risk: str = ""


class RuleOut(ApiModel):
    key: str
    stage_name: str
    description: str
    required: list[RuleRequirement]
    allowed: list[EquipmentType]
    unexpected: list[RuleUnexpected]
    confirm_after_snapshots: int


class RuleIn(ApiModel):
    stage_name: str | None = Field(default=None, min_length=2, max_length=200)  # поля нет — название прежнее
    description: str | None = Field(default=None, max_length=1000)
    required: list[RuleRequirement]
    allowed: list[EquipmentType] = []
    unexpected: list[RuleUnexpected] = []
    confirm_after_snapshots: int = Field(ge=1, le=24)


class RuleCreate(RuleIn):
    """Новое правило: достаточно названия этапа — технику добавляют потом в редакторе правила."""

    stage_name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=1000)
    required: list[RuleRequirement] = []
    confirm_after_snapshots: int = Field(default=3, ge=1, le=24)


def rule_out(r: Rule) -> RuleOut:
    return RuleOut(
        key=r.key,
        stage_name=r.stage_name,
        description=r.description,
        confirm_after_snapshots=r.confirm_after,
        required=[RuleRequirement(type=i.equipment_type, min=i.min_count, why=i.why, risk=i.risk) for i in r.of_kind("required")],
        allowed=[i.equipment_type for i in r.of_kind("allowed")],
        unexpected=[RuleUnexpected(type=i.equipment_type, why=i.why, risk=i.risk) for i in r.of_kind("unexpected")],
    )


# ---------- камеры ----------
class CameraOut(ApiModel):
    id: str
    site_id: str
    zone_id: str
    name: str
    online: bool  # включена и на связи
    enabled: bool
    spider_enabled: bool
    status: Literal["online", "offline", "unknown"]
    source_type: Literal["rtsp"]
    address: str | None  # без логина и пароля
    has_credentials: bool
    demo: bool  # смотрит на демо-ролик шлюза
    stream_path: str  # поток в шлюзе видео: WebRTC по адресу {videoUrl}/{streamPath}/whep
    last_error: str | None
    last_snapshot_at: datetime | None
    scene: str


def camera_out(c: Camera) -> CameraOut:
    from app.services.video import camera_path, demo_clip_of

    address = CameraAddress(c.scheme or "rtsp", c.host, c.port or 554, c.path or "/").display if c.host else None
    return CameraOut(
        id=c.id,
        site_id=c.site_id,
        zone_id=c.zone_id,
        name=c.name,
        enabled=c.enabled,
        spider_enabled=c.spider_enabled,
        status=c.status,
        online=c.enabled and c.status != "offline",
        source_type="rtsp",
        address=address,
        has_credentials=bool(c.username),
        demo=demo_clip_of(c.path) is not None,
        stream_path=camera_path(c.id),
        last_error=c.last_error,
        last_snapshot_at=c.last_snapshot_at,
        scene=c.scene,
    )


class ConnectionIn(ApiModel):
    protocol: Literal["rtsp"] = "rtsp"
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str = Field(default="/", max_length=500)
    username: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=200)


class CameraIn(ApiModel):
    site_id: str
    name: str = Field(min_length=2, max_length=200)
    zone_id: str | None = None
    new_zone_name: str | None = Field(default=None, max_length=200)  # создать зону, если подходящей нет
    new_zone_kind: Literal["work", "gate", "storage"] = "work"
    connection: ConnectionIn
    allow_offline: bool = False  # сохранить, даже если камера сейчас не отвечает
    spider_enabled: bool = False


class CameraPatch(ApiModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    enabled: bool | None = None
    zone_id: str | None = None
    spider_enabled: bool | None = None
    connection: ConnectionIn | None = None  # новый адрес; пароль пустой — оставить прежний


class ProbeOut(ApiModel):
    ok: bool
    code: str
    message: str
    elapsed_ms: int
    preview_path: str | None = None  # временный поток в шлюзе: видео для предпросмотра в форме (живёт 10 минут)


class LiveCameraOut(ApiModel):
    """Что видит анализ на камере прямо сейчас (обновляется каждые 2 секунды)."""

    camera_id: str
    online: bool
    error: str | None
    received_at: datetime | None  # последний кадр из видео
    analyzed_at: datetime | None  # последний разобранный кадр
    analyzed: bool | None  # False — сервис анализа не смог разобрать кадр
    note: str | None
    detections: list["DetectionOut"]
    counts: dict[str, int]


# ---------- снимки ----------
class BoxOut(ApiModel):
    x: float
    y: float
    w: float
    h: float


class DetectionOut(ApiModel):
    id: str
    type: EquipmentType
    confidence: float
    box: BoxOut
    moving: bool | None = None


class SnapshotOut(ApiModel):
    id: str
    camera_id: str
    taken_at: datetime
    image_url: str
    detections: list[DetectionOut]
    analyzed: bool
    provider: str | None
    note: str | None


def snapshot_out(s: Snapshot) -> SnapshotOut:
    return SnapshotOut(
        id=s.id,
        camera_id=s.camera_id,
        taken_at=s.taken_at,
        image_url=s.image_url,
        analyzed=s.analyzed,
        provider=s.provider,
        note=s.note,
        detections=[
            DetectionOut(
                id=f"d{d.id}",
                type=d.equipment_type,
                confidence=d.confidence,
                moving=d.moving,
                box=BoxOut(x=d.x, y=d.y, w=d.w, h=d.h),
            )
            for d in s.detections
        ],
    )


# ---------- предупреждения ----------
AlertStatus = Literal["new", "acknowledged", "confirmed", "prescribed", "resolved", "false_positive"]


class AlertEventOut(ApiModel):
    at: datetime
    who: str
    text: str


class AlertOut(ApiModel):
    id: str
    code: str
    site_id: str
    zone_id: str
    stage_id: str | None
    camera_id: str | None
    kind: Literal["missing", "count_below", "unexpected", "idle", "camera_offline"]
    severity: Literal["high", "medium", "low"]
    status: AlertStatus
    is_open: bool
    title: str
    summary: str
    consequence: str
    advice: str
    equipment: EquipmentType | None
    expected: int | None
    observed: int | None
    prescription_no: str | None
    prescription_due: date | None
    started_at: datetime
    updated_at: datetime
    evidence: list[str]
    evidence_snapshots: list[SnapshotOut]
    history: list[AlertEventOut]


def alert_code(a: Alert) -> str:
    # год по местному времени: отклонение в 01:00 1 января иначе получало номер прошлого года
    return f"ОТК-{a.started_at.astimezone(_TZ).year % 100:02d}-{a.number:04d}"


def alert_out(a: Alert) -> AlertOut:
    return AlertOut(
        id=a.id,
        code=alert_code(a),
        site_id=a.site_id,
        zone_id=a.zone_id,
        stage_id=a.stage_id,
        camera_id=a.camera_id,
        kind=a.kind,
        severity=a.severity,
        status=a.status,
        is_open=a.status in OPEN_STATUSES,
        title=a.title,
        summary=a.summary,
        consequence=a.consequence,
        advice=a.advice,
        equipment=a.equipment_type,
        expected=a.expected,
        observed=a.observed,
        prescription_no=a.prescription_no,
        prescription_due=a.prescription_due,
        started_at=a.started_at,
        updated_at=a.updated_at,
        evidence=[s.id for s in a.evidence],
        evidence_snapshots=[snapshot_out(s) for s in a.evidence],
        history=[AlertEventOut(at=e.at, who=e.who, text=e.text) for e in a.events],
    )


class AlertActionIn(ApiModel):
    status: AlertStatus
    comment: str = Field(default="", max_length=1000)
    due_date: date | None = None  # срок устранения — только для предписания


# ---------- сверка ----------
class CheckRow(ApiModel):
    type: EquipmentType
    need: int
    have: int
    state: Literal["ok", "low", "missing", "not_detected"]  # not_detected — модель такую технику не распознаёт
    why: str


class ExtraRow(ApiModel):
    type: EquipmentType
    have: int
    why: str


class EquipmentCheckOut(ApiModel):
    """Сравнение «нужно по плану / видим на камерах» для текущего этапа объекта."""

    site_id: str
    stage_id: str | None
    stage_name: str | None
    coverage: bool  # есть ли свежий кадр рабочей зоны
    working: bool  # идёт ли рабочее время объекта: вне его технику не сверяем
    work_hours: str  # рабочее время словами: «8:00–20:00, пн–сб»
    checked_at: datetime | None
    rows: list[CheckRow]
    extra: list[ExtraRow]  # техника не по этапу
    arriving: dict[str, int]  # техника на въезде и складе — «подъезжает», в норму не засчитывается
    estimated_observed: dict[str, int] | None = None  # отдельная приблизительная оценка по пересечениям
    estimated_site_observed: dict[str, int] | None = None  # все зоны объекта, включая въезд и склад
    overlap_matches: int = 0


class CheckRunOut(ApiModel):
    id: str
    site_id: str
    at: datetime
    trigger: str
    stage_id: str | None
    coverage: bool
    observed: dict[str, int]
    arriving: dict[str, int]
    violations: list[dict]


def check_out(c: CheckRun) -> CheckRunOut:
    return CheckRunOut(
        id=c.id,
        site_id=c.site_id,
        at=c.at,
        trigger=c.trigger,
        stage_id=c.stage_id,
        coverage=c.coverage,
        observed=c.observed,
        arriving=c.arriving,
        violations=c.violations,
    )


class AuditOut(ApiModel):
    id: int
    at: datetime
    actor_login: str
    actor_name: str
    actor_role: str
    action: str
    entity_type: str
    entity_id: str | None
    entity_name: str
    summary: str
    details: dict
    ip: str


def audit_out(e) -> AuditOut:  # noqa: ANN001
    return AuditOut(
        id=e.id, at=e.at, actor_login=e.actor_login, actor_name=e.actor_name, actor_role=e.actor_role, action=e.action,
        entity_type=e.entity_type, entity_id=e.entity_id, entity_name=e.entity_name, summary=e.summary,
        details=e.details or {}, ip=e.ip,
    )  # fmt: skip


class IngestOut(ApiModel):
    accepted: bool  # False — кадр старее уже полученного с этой камеры: картину он не меняет
    detections: int
    note: str



# ---------- журнал наблюдения техники от tracker ----------
class EquipmentEventIn(ApiModel):
    """Lifecycle-сообщение producer. site_id намеренно отсутствует: его знает камера."""

    event_id: UUID
    event_type: Literal["FIRST_SEEN", "PRESENT", "LAST_SEEN", "INTERRUPTED"]
    camera_id: str = Field(min_length=1, max_length=40)
    tracker_session_id: UUID
    track_id: str = Field(min_length=1, max_length=255)
    equipment_type: EquipmentType
    observed_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    reason: str | None = Field(default=None, max_length=64)
    model_version: str | None = Field(default=None, max_length=80)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def _valid_lifecycle_times(self) -> "EquipmentEventIn":
        values = (self.observed_at, self.first_seen_at, self.last_seen_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            raise ValueError("все timestamps должны содержать часовой пояс")
        if not self.first_seen_at <= self.last_seen_at <= self.observed_at:
            raise ValueError("ожидается first_seen_at <= last_seen_at <= observed_at")
        return self


class EquipmentEventAck(ApiModel):
    accepted: bool = True
    event_id: str
    visit_id: str
    duplicate: bool


class EquipmentEventOut(ApiModel):
    event_id: str
    camera_id: str
    tracker_session_id: str
    track_id: str
    visit_id: str
    equipment_class: EquipmentType
    event_type: Literal["appeared", "disappeared"]
    timestamp: datetime
    confidence: float | None
    received_at: datetime


def equipment_event_out(row: EquipmentEvent) -> EquipmentEventOut:
    return EquipmentEventOut(
        event_id=row.event_id,
        camera_id=row.camera_id,
        tracker_session_id=row.tracker_session_id,
        track_id=row.track_id,
        visit_id=row.visit_id,
        equipment_class=row.equipment_class,
        event_type=row.event_type,
        timestamp=row.timestamp,
        confidence=row.confidence,
        received_at=row.received_at,
    )


class EquipmentVisitOut(ApiModel):
    id: str
    camera_id: str
    tracker_session_id: str
    track_id: str
    equipment_class: EquipmentType
    first_seen_at: datetime
    last_seen_at: datetime
    duration_seconds: float
    status: Literal["active", "completed", "lost"]
    confidence: float | None
    closed_at: datetime | None
    close_reason: str | None
    has_observation_gap: bool


def equipment_visit_out(row: EquipmentVisit) -> EquipmentVisitOut:
    return EquipmentVisitOut(
        id=row.id,
        camera_id=row.camera_id,
        tracker_session_id=row.tracker_session_id,
        track_id=row.track_id,
        equipment_class=row.equipment_class,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        duration_seconds=row.duration_seconds,
        status=row.status,
        confidence=row.confidence,
        closed_at=row.closed_at,
        close_reason=row.close_reason,
        has_observation_gap=row.has_observation_gap,
    )


class EquipmentEventsPageOut(ApiModel):
    items: list[EquipmentEventOut]
    total: int
    limit: int
    offset: int


class EquipmentVisitsPageOut(ApiModel):
    items: list[EquipmentVisitOut]
    total: int
    limit: int
    offset: int


# ---------- сохранённый источник Camera Stage Monitor ----------


class SpiderPlannedValueOut(ApiModel):
    value: float | int | None
    unit: str | None


class SpiderEquipmentPlanOut(ApiModel):
    source_name: str
    planned_quantity: float | int | None
    unit_productivity: float | None = None
    limitations: list[str]


class SpiderResourceStageOut(ApiModel):
    code: str
    name: str
    start: datetime
    finish: datetime
    planned_work_shifts: float | int | None
    planned_volume: SpiderPlannedValueOut
    planned_productivity: SpiderPlannedValueOut
    equipment: list[SpiderEquipmentPlanOut]


class SpiderResourcesOut(ApiModel):
    stages: list[SpiderResourceStageOut]
    limitations: list[str]




class SpiderConnectionIn(ApiModel):
    url: str = Field(min_length=1, max_length=500)
    token: str | None = None


class SpiderConnectionOut(ApiModel):
    url: str | None
    has_token: bool
    configured: bool
    custom: bool
class SpiderImportOut(ApiModel):
    id: str
    status: Literal["pending", "succeeded", "failed"]
    snapshot_id: str | None
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None


class SpiderSnapshotOut(ApiModel):
    id: str
    resource_revision_id: str
    source_url: str
    api_version: str
    data_source: str | None
    data_type: str | None
    warning: str | None
    created_at: datetime
    resources: SpiderResourcesOut
    source_observations: list[dict]
    manual_annotation_type: str | None
    manual_requires_validation: bool | None
    manual_annotations: list[dict]
    source_comparisons: list[dict]


class SpiderOut(ApiModel):
    snapshot: SpiderSnapshotOut | None
    last_import: SpiderImportOut | None
    last_success_at: datetime | None
    stale: bool
    limitations: list[str]




class SpiderPrepareIn(ApiModel):
    snapshot_id: str = Field(min_length=64, max_length=64)


class SpiderObservationAssetOut(ApiModel):
    id: str
    snapshot_id: str
    observation_id: str
    image_sha256: str
    media_type: str
    width: int
    height: int
    observed_at: datetime
    timestamp_quality: str
    fetched_at: datetime
    image_url: str
    target: dict | None
    target_error: str | None
    data_type: str | None
    warning: str | None

class DeviationOut(ApiModel):
    kind: Literal["missing", "count_below", "unexpected"]
    type: EquipmentType
    need: int | None
    have: int
    title: str
    why: str


class AnalyzeOut(ApiModel):
    """Результат разбора одного снимка: техника + сверка с правилом этапа."""

    provider: str
    model: str | None
    supported: bool
    note: str | None
    elapsed_ms: int
    image_url: str | None
    detections: list[DetectionOut]
    rule_key: str
    stage_name: str
    rows: list[CheckRow]
    deviations: list[DeviationOut]


# ---------- отчёт ----------
class DayCount(ApiModel):
    date: date
    label: str
    count: int


class SiteCount(ApiModel):
    site_id: str
    name: str
    count: int
    high: int


class NamedCount(ApiModel):
    key: str
    count: int


class WeeklyReportOut(ApiModel):
    date_from: date
    date_to: date
    total: int
    open: int
    resolved: int
    false_positive: int
    by_day: list[DayCount]
    by_site: list[SiteCount]
    by_kind: list[NamedCount]
    by_equipment: list[NamedCount]


# ---------- рамки в реальном времени ----------
class TrackerCameraOut(ApiModel):
    """Камера для сервиса разметки: какой поток читать из шлюза."""

    id: str
    name: str
    site_id: str
    zone_kind: str  # work — рабочая зона, gate — въезд, storage — склад
    rtsp_url: str  # логин шлюза sk-tracker, пароль — ключ сервиса
    demo_clip: str | None  # камера смотрит демо-ролик (для имитации сервиса)


class EquipmentUsageOut(ApiModel):
    """Сколько работала техника за час на одной камере (по рамкам сервиса разметки)."""

    hour: datetime  # начало часа
    camera_id: str
    zone_kind: str  # work | gate | storage
    type: EquipmentType
    max_count: int  # сколько машин этого типа было в кадре одновременно
    present_min: float  # сколько минут тип был в кадре
    moving_min: float  # из них двигался


# ---------- работы по камерам: ответы сервисов аналитики ----------
AnalyticsService = Literal["deterministic", "vlm_llm"]


class WorkRefOut(ApiModel):
    """Пункт плана в ответе сервиса: наша работа (step_key) и вид работ по справочнику (stage_id)."""

    step_key: str
    stage_id: int | None
    name: str  # название работы в нашем плане; удалена — название вида работ из справочника
    in_plan: bool  # работа всё ещё есть в плане


class WorkEvidenceOut(ApiModel):
    source: str  # cv_detection | visual_observation | visual_relation | progress_event
    role: str  # supports | contradicts
    explanation: str


class WorkGroupOut(ApiModel):
    """Одна операция на кадре: specific — один кандидат, ambiguous — альтернативы (одна из них)."""

    match: str
    works: list[WorkRefOut]
    visual_state: str  # operation_indicated | presence_or_result_only | not_evaluated
    explanation: str
    evidence: list[WorkEvidenceOut]
    area: list[float] | None  # [x_min, y_min, x_max, y_max] в долях кадра


class TransitionOut(ApiModel):
    """Следующая работа по технике (сервис «по технике»): possible_start — похоже, она началась."""

    status: str
    current: WorkRefOut | None
    next: WorkRefOut | None
    first_at: datetime | None
    last_at: datetime | None
    points: int  # сколько разных моментов (через 15+ минут) видна техника следующей работы


class ScheduleItemOut(ApiModel):
    work: WorkRefOut
    status: str  # possible_delay | no_delay_indicated | insufficient_evidence
    reason: str  # reason_code: open_state_after_deadline, deadline_not_reached…
    overdue_s: int | None
    evidence_at: datetime | None


class ScheduleOut(ApiModel):
    status: str  # possible_delay | no_delay_indicated | insufficient_evidence | not_evaluated
    items: list[ScheduleItemOut]


ResourceReasonCode = Literal[
    "no_resource_plan",
    "no_resource_target",
    "scope_unknown",
    "resource_scope_unknown",
    "plan_class_unmapped",
    "duplicate_planned_class",
    "planned_quantity_unknown",
    "observation_unavailable",
    "partial_coverage",
    "unknown_coverage",
    "duplicate_observed_class",
    "observation_class_missing",
    "observation_class_unmapped",
    "non_production_source",
    "timestamp_unverified",
    "observation_requires_validation",
    "demonstration_mode",
    "no_independent_measurements",
    "no_planned_equipment",
    "vlm_resources_not_evaluated",
]
ResourceId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
ResourcePointer = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"^/")]
ResourceLimitation = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"\S")]


class ResourceOut(ApiModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResourceMeasureOut(ResourceOut):
    value: float = Field(ge=0, le=1_000_000_000_000_000, allow_inf_nan=False)
    unit: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]


class ResourceEquipmentOut(ResourceOut):
    item_id: ResourceId
    class_code: ResourceId | None
    planned_quantity: int | None = Field(ge=0, le=1_000_000)
    visible_count: int | None = Field(ge=0, le=1_000_000)
    visible_count_delta: int | None = Field(ge=-1_000_000, le=1_000_000)
    status: Literal["visible_below_plan", "visible_equal_plan", "visible_above_plan", "unknown"]
    reason_codes: list[ResourceReasonCode] = Field(max_length=20)
    evidence_refs: list[ResourcePointer] = Field(max_length=205)


class ResourceAssessmentOut(ResourceOut):
    status: Literal["compared", "demonstration", "insufficient_evidence", "not_evaluated"]
    reason_codes: list[ResourceReasonCode] = Field(max_length=30)
    stage_code: ResourceId | None
    selection_basis: Literal["planned_at_frame_time", "confirmed_current", "explicit"] | None
    basis: Literal["cv_detections", "manual_visual_estimate", "unavailable"]
    coverage: Literal["full_scope", "partial_scope", "unknown"]
    equipment_items: list[ResourceEquipmentOut] = Field(max_length=200)
    planned_volume: ResourceMeasureOut | None
    planned_work_shifts: float | None = Field(gt=0, le=1_000_000)
    planned_productivity: ResourceMeasureOut | None
    actual_volume: None
    actual_productivity: None
    evidence_refs: list[ResourcePointer] = Field(max_length=10)
    limitations: list[ResourceLimitation] = Field(min_length=1, max_length=10)


class ResourceEvidenceOut(ApiModel):
    pointer: ResourcePointer
    description: Annotated[str, Field(min_length=1, max_length=1000)]
    available: bool


class ServiceAnswerOut(ApiModel):
    """Последний ответ одного сервиса по кадру камеры (или почему его нет)."""

    service: AnalyticsService
    state: Literal["pending", "done", "error", "unknown"]
    at: datetime | None  # когда пришёл ответ (или отказ)
    observed_at: datetime | None  # когда снят кадр, о котором ответ
    outcome: str | None  # assessed | insufficient_evidence | outside_plan | no_plan | scope_unknown
    groups: list[WorkGroupOut]
    transition: TransitionOut | None
    schedule: ScheduleOut | None
    limitations: list[str]
    model: str | None  # версия сервиса и моделей — мелким шрифтом
    error_code: str | None
    error: str | None  # почему нет ответа — руководителю и администратору
    newer_pending: bool  # по более свежему кадру уже спросили, ждём ответ
    resource_assessment: ResourceAssessmentOut | None
    analysis_mode: Literal["demonstration", "operational"] | None
    resource_evidence: list[ResourceEvidenceOut]


class CameraWorkOut(ApiModel):
    camera_id: str
    camera_name: str
    zone_name: str
    sent_at: datetime | None  # когда последний раз отправляли кадр
    image_url: str | None  # этот кадр, если он ещё хранится
    answers: list[ServiceAnswerOut]
    matches_plan: bool | None  # хоть один кандидат — работа, которая сегодня идёт по графику; None — не с чем сравнить


class SiteWorkOut(ApiModel):
    source_configured: bool

    enabled: bool  # подключён хоть один сервис
    services: list[AnalyticsService]
    can_run: bool  # может ли пользователь отправить кадры сейчас (руководитель, администратор)
    running: bool  # сейчас идёт отправка по этому объекту
    llm_waiting: int  # сколько запросов к VLM ждут глобальный лимит
    planned: list[str]  # работы, которые сегодня идут по графику
    plan_issue: str | None  # почему план не уходит сервисам (не у всех работ выбран вид по справочнику…)
    problem: str | None  # почему последний кадр не ушёл (справочник недоступен, кадр повреждён…)
    cameras: list[CameraWorkOut]
    catalog_version: str | None
    next_at: datetime | None  # следующая плановая отправка


class CatalogWorkOut(ApiModel):
    stage_id: int
    name: str
    path: list[str]  # разделы справочника над работой
    kind: Literal["concrete", "no_class"]  # no_class — работа без техники: по кадрам не видна, сервисы следят за сроками


class AnalyticsCatalogOut(ApiModel):
    """Виды работ справочника сервисов аналитики — для поля «Вид работ по справочнику» в плане."""

    enabled: bool  # подключены ли сервисы
    version: str | None
    object_type: str | None  # вид объекта по справочнику; None — неизвестен, показаны все работы
    works: list[CatalogWorkOut]
    error: str | None  # справочник не получен (или показан сохранённый: сервисы не ответили)


class TrackerStatusOut(ApiModel):
    """Откуда рамки в реальном времени и что с ними — для отладки (только администратору и руководителю)."""

    enabled: bool
    source: str | None  # model — своя модель по видео камер; service — внешний сервис разметки; None — рамок нет
    connected: bool
    messages: int  # сколько сообщений с рамками было с запуска сервера
    last_message_at: datetime | None
    problem: str | None  # внешний сервис: последняя ошибка формата (рамка в долях 0–1, нет track_id…)
    problem_at: datetime | None
    viewers: int  # сколько браузеров сейчас смотрят рамки
    live_cameras: list[str]  # своя модель: камеры, чьё видео она сейчас разбирает (их кто-то смотрит)
    model: str | None  # своя модель: название
    model_device: str | None  # на чём считает: CPU, CoreML, CUDA
    model_ms: float | None  # сколько в среднем занимает один кадр
