"""Модель данных: пользователи, объекты, камеры, календарный план, правила, кадры, проверки, отклонения, журнал действий."""

import secrets
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, Column, Date, ForeignKey, Index, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


# Вид объекта — как в «Справочнике видов работ» организаторов (у дороги и у школы разные работы и техника); уходит
# сервисам аналитики как object_type_code. public — социальный объект, вид которого не уточнили; industrial и other
# в справочнике нет — сервисам уходит «вид неизвестен»
SITE_KINDS = (
    "housing",  # жильё
    "education",  # школа, колледж
    "preschool",  # детский сад
    "healthcare",  # больница, поликлиника
    "sports",
    "culture",
    "administrative",
    "office",  # офисно-деловой центр
    "roads",
    "public",
    "industrial",
    "other",
)

# Структура таблиц меняется только вместе с миграцией: uv run alembic revision --autogenerate -m "…" (см. alembic.ini)

user_sites = Table(
    "user_sites",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("site_id", ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
)

alert_evidence = Table(
    "alert_evidence",
    Base.metadata,
    Column("alert_id", ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
    Column("snapshot_id", ForeignKey("snapshots.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))  # foreman | manager | inspector | admin
    phone: Mapped[str] = mapped_column(String(32), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)

    sites: Mapped[list["Site"]] = relationship(secondary=user_sites, lazy="selectin")


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(200), default="")
    contractor: Mapped[str] = mapped_column(String(200), default="")
    foreman_name: Mapped[str] = mapped_column(String(120), default="")
    # вид объекта: у дороги и у дома разные этапы и техника — это подсказка сервису, определяющему этап по кадрам
    kind: Mapped[str] = mapped_column(String(16), default="other", server_default="other")  # см. SITE_KINDS
    position: Mapped[int] = mapped_column(default=0)
    # Рабочее время: вне его технику не сверяем (ночью её нет — это не отклонение) и кадры сервисам аналитики не шлём.
    # Часы местные, work_to не входит: 8–20 — до 20:00; 0–24 — круглосуточно. work_days — пн…вс, «1» — рабочий день
    work_from: Mapped[int] = mapped_column(default=0, server_default="0")
    work_to: Mapped[int] = mapped_column(default=24, server_default="24")
    work_days: Mapped[str] = mapped_column(String(7), default="1111111", server_default="1111111")
    # выполнение объекта не хранится: его считают по календарному плану (services/plan.py)


class SpiderConnection(Base):
    """Явно заданное для объекта подключение Camera Stage Monitor."""

    __tablename__ = "spider_connections"

    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True)
    source_url: Mapped[str] = mapped_column(String(500))
    token_enc: Mapped[str | None] = mapped_column(Text)  # зашифрован; API сообщает только наличие




class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # work — рабочая зона (техника считается работающей), gate — въезд (техника «подъезжает»), storage — склад
    kind: Mapped[str] = mapped_column(String(16), default="work")
    position: Mapped[int] = mapped_column(default=0)


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"))
    name: Mapped[str] = mapped_column(String(200))

    # Источник — всегда видеопоток RTSP: его забирает шлюз видео. Демо-камеры смотрят на демо-ролики в том же шлюзе.
    source_type: Mapped[str] = mapped_column(String(8), default="rtsp")
    scheme: Mapped[str | None] = mapped_column(String(8), default="rtsp")
    host: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None]
    path: Mapped[str | None] = mapped_column(String(500))
    username: Mapped[str | None] = mapped_column(String(120))
    password_enc: Mapped[str | None] = mapped_column(Text)  # зашифрован, наружу не отдаётся
    scene: Mapped[str] = mapped_column(String(16), default="yard")  # фон-заглушка, пока видео не пришло

    enabled: Mapped[bool] = mapped_column(default=True)
    # Spider для видео включают отдельно: новые камеры не отправляют контекст источника по умолчанию.
    spider_enabled: Mapped[bool] = mapped_column(default=False, server_default="0")

    status: Mapped[str] = mapped_column(String(10), default="unknown")  # online | offline | unknown
    last_error: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_snapshot_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    position: Mapped[int] = mapped_column(default=0)

    zone: Mapped[Zone] = relationship(lazy="joined")


class Rule(Base):
    """Правило методики: «этап работ → необходимая техника»."""

    __tablename__ = "rules"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    stage_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    confirm_after: Mapped[int] = mapped_column(default=3)  # сколько проверок подряд подтверждают отклонение
    position: Mapped[int] = mapped_column(default=0)

    items: Mapped[list["RuleItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="RuleItem.position")

    def of_kind(self, kind: str) -> list["RuleItem"]:
        return [i for i in self.items if i.kind == kind]


class RuleItem(Base):
    __tablename__ = "rule_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_key: Mapped[str] = mapped_column(ForeignKey("rules.key", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # required | allowed | unexpected
    equipment_type: Mapped[str] = mapped_column(String(20))
    min_count: Mapped[int] = mapped_column(default=1)
    why: Mapped[str] = mapped_column(Text, default="")  # зачем техника нужна / почему она лишняя
    risk: Mapped[str] = mapped_column(Text, default="")  # чем грозит отклонение — попадает в текст предупреждения
    severity: Mapped[str | None] = mapped_column(String(8))  # переопределяет важность по умолчанию
    position: Mapped[int] = mapped_column(default=0)


class Stage(Base):
    """Этап календарного плана. Уровень 1 — укрупнённый, уровень 2 — работы, к которым привязано правило."""

    __tablename__ = "stages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("stages.id"))
    level: Mapped[int] = mapped_column(default=2)
    name: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    rule_key: Mapped[str | None] = mapped_column(ForeignKey("rules.key"))
    position: Mapped[int] = mapped_column(default=0)
    # сколько сделано по факту, % — отмечают руководитель и администратор; у этапа с работами считается по работам
    fact_progress: Mapped[int] = mapped_column(default=0, server_default="0")
    fact_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # вид работ по справочнику сервисов аналитики (stage_id) и версия справочника, по которой его выбрали. Сопоставляет
    # человек: по похожему названию автоматически нельзя. Не у всех работ плана — план сервисам не отправляется
    catalog_stage_id: Mapped[int | None]
    catalog_version: Mapped[str | None] = mapped_column(String(64))

    def status_on(self, day: date) -> str:
        if day > self.end_date:
            return "done"
        return "in_progress" if day >= self.start_date else "planned"

    def plan_progress_on(self, day: date) -> int:
        """Сколько процентов должно быть сделано к концу этого дня по графику — равномерно по дням этапа."""
        if day < self.start_date:
            return 0
        total = (self.end_date - self.start_date).days + 1
        return min(100, round(((day - self.start_date).days + 1) * 100 / total))


class CheckRun(Base):
    """Проверка объекта: что увидели камеры и какие отклонения от правила этапа найдены."""

    __tablename__ = "check_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual | seed | ingest | camera_added | rule_change
    stage_id: Mapped[str | None] = mapped_column(ForeignKey("stages.id"))
    coverage: Mapped[bool] = mapped_column(default=True)  # есть ли свежий кадр рабочей зоны
    observed: Mapped[dict] = mapped_column(JSON, default=dict)  # техника в рабочих зонах: {тип: количество}
    arriving: Mapped[dict] = mapped_column(JSON, default=dict)  # техника на въезде и складе
    violations: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (Index("ix_check_runs_site_at", "site_id", "at"),)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    check_id: Mapped[str | None] = mapped_column(ForeignKey("check_runs.id", ondelete="SET NULL"))
    taken_at: Mapped[datetime] = mapped_column(UTCDateTime)
    image_url: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(10), default="capture")  # seed | capture | ingest | photo
    # Пользователь явно выбирает, добавлять ли Spider к независимому анализу загруженного фото.
    photo_use_spider: Mapped[bool | None] = mapped_column()
    analyzed: Mapped[bool] = mapped_column(default=True)  # False — анализ не удался или кадр незнаком демо-анализатору
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))  # какая модель нашла технику — уходит сервисам аналитики
    analysis_ms: Mapped[int | None]
    note: Mapped[str | None] = mapped_column(Text)

    detections: Mapped[list["Detection"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="Detection.id")

    __table_args__ = (
        Index("ix_snapshots_camera_taken", "camera_id", "taken_at"),
        Index("ix_snapshots_site_taken", "site_id", "taken_at"),
    )


class EquipmentUsage(Base):
    """Сколько работала техника: по часам, камерам и типам — из рамок сервиса разметки (треки 10–15 раз в секунду).
    present_s — сколько секунд этот тип был в кадре, moving_s — из них двигался, max_count — сколько машин сразу."""

    __tablename__ = "equipment_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    zone_kind: Mapped[str] = mapped_column(String(16))  # вид зоны камеры на момент записи
    hour: Mapped[datetime] = mapped_column(UTCDateTime)  # начало часа
    equipment_type: Mapped[str] = mapped_column(String(20))
    max_count: Mapped[int] = mapped_column(default=0)
    present_s: Mapped[float] = mapped_column(default=0.0)
    moving_s: Mapped[float] = mapped_column(default=0.0)

    __table_args__ = (
        UniqueConstraint("camera_id", "hour", "equipment_type", name="uq_equipment_usage_camera_hour_type"),
        Index("ix_equipment_usage_site_hour", "site_id", "hour"),
    )


class EquipmentVisit(Base):
    """Сглаженный интервал видимости одного tracker track на одной камере и в одной сессии."""

    __tablename__ = "equipment_visits"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    tracker_session_id: Mapped[str] = mapped_column(String(36))
    track_id: Mapped[str] = mapped_column(String(255))
    equipment_class: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | completed | lost
    confidence: Mapped[float | None]
    last_message_at: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    close_reason: Mapped[str | None] = mapped_column(String(64))
    has_observation_gap: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "camera_id",
            "tracker_session_id",
            "track_id",
            "first_seen_at",
            name="uq_equipment_visits_camera_session_track_first",
        ),
        CheckConstraint("duration_seconds >= 0", name="ck_equipment_visits_duration_nonnegative"),
        CheckConstraint("last_seen_at >= first_seen_at", name="ck_equipment_visits_last_not_before_first"),
        CheckConstraint("status IN ('active', 'completed', 'lost')", name="ck_equipment_visits_status"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_equipment_visits_confidence",
        ),
        Index("ix_equipment_visits_site_last_seen", "site_id", "last_seen_at"),
        Index("ix_equipment_visits_status_last_seen", "status", "last_seen_at"),
    )


class EquipmentEvent(Base):
    """Граница интервала наблюдения: только appeared/disappeared, без heartbeat-шума."""

    __tablename__ = "equipment_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    tracker_session_id: Mapped[str] = mapped_column(String(36))
    track_id: Mapped[str] = mapped_column(String(255))
    visit_id: Mapped[str] = mapped_column(ForeignKey("equipment_visits.id", ondelete="CASCADE"))
    equipment_class: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(16))  # appeared | disappeared
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime)
    confidence: Mapped[float | None]
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    payload_sha256: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint("event_type IN ('appeared', 'disappeared')", name="ck_equipment_events_type"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_equipment_events_confidence",
        ),
        Index("ix_equipment_events_site_timestamp", "site_id", "timestamp"),
        Index(
            "ix_equipment_events_camera_session_track_timestamp",
            "camera_id",
            "tracker_session_id",
            "track_id",
            "timestamp",
        ),
    )




class SpiderSnapshot(Base):
    """Неизменяемый ответ Camera Stage Monitor, адресуемый отпечатком исходных документов."""

    __tablename__ = "spider_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_url: Mapped[str] = mapped_column(String(500))
    api_version: Mapped[str] = mapped_column(String(20))
    data_source: Mapped[str | None] = mapped_column(String(120))
    data_type: Mapped[str | None] = mapped_column(String(120))
    warning: Mapped[str | None] = mapped_column(Text)
    documents: Mapped[dict] = mapped_column(JSON)
    resources: Mapped[dict] = mapped_column(JSON)
    normalization_version: Mapped[str] = mapped_column(String(20), default="1", server_default="1")
    resource_revision_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SpiderImport(Base):
    """Одна попытка обновить источник для объекта; неуспех не меняет последний снимок."""

    __tablename__ = "spider_imports"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str] = mapped_column(String(500))
    connection_fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # pending | succeeded | failed
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("spider_snapshots.id"))
    fetches: Mapped[list] = mapped_column(JSON, default=list)
    partial_documents: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_spider_imports_status"),
        Index("ix_spider_imports_site_started", "site_id", "started_at"),
    )


class SpiderObservationAsset(Base):
    """Сохранённое исходное фото Spider и проверенный выбор этапа для одного observation."""

    __tablename__ = "spider_observation_assets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("spider_snapshots.id", ondelete="CASCADE"))
    connection_fingerprint: Mapped[str | None] = mapped_column(String(64))
    observation_id: Mapped[str] = mapped_column(String(120))
    image_sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(32))
    width: Mapped[int] = mapped_column()
    height: Mapped[int] = mapped_column()
    storage_path: Mapped[str] = mapped_column(String(500))
    source_image_url: Mapped[str] = mapped_column(String(1000))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    timestamp_quality: Mapped[str] = mapped_column(String(32), default="unknown", server_default="unknown")
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    target_document: Mapped[dict | None] = mapped_column(JSON)
    target_error: Mapped[str | None] = mapped_column(String(64))
    target_attempts: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (
        UniqueConstraint(
            "site_id",
            "snapshot_id",
            "observation_id",
            "image_sha256",
            "connection_fingerprint",
            name="uq_spider_observation_assets_connection_image",
        ),
    )
class Observation(Base):
    """Журнал наблюдений для сервисов аналитики: кадр камеры раз в analytics_observation_min минут — без картинки.

    Кадры с картинками чистятся через несколько часов (keep_frames_per_camera), а сервисам нужна история за неделю:
    какая техника была видна и когда. Отпечаток кадра (sha256) остаётся — по нему сервис сверяет, что это тот же кадр.
    detections — техника в наших типах: [{"id", "type", "confidence", "box": [x, y, w, h] в процентах кадра}]."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    image_id: Mapped[str] = mapped_column(String(40))  # id снимка: сам снимок удалят раньше
    zone_kind: Mapped[str] = mapped_column(String(16))  # вид зоны камеры на момент записи
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    image_sha256: Mapped[str] = mapped_column(String(64))
    analyzed: Mapped[bool] = mapped_column(default=True)  # False — анализ кадра не удался: CV «failed»
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))
    detections: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (
        UniqueConstraint("camera_id", "image_id", name="uq_observations_camera_image"),
        Index("ix_observations_site_observed", "site_id", "observed_at"),
    )


class AnalyticsRequest(Base):
    """Точный вход одной отправки одного сервиса аналитики по кадру камеры или загруженному фото.

    id — request_id этой отправки; metadata_json — JSON ровно в том виде, как ушёл
    (по нему считается input_sha256, с ним же повторяют запрос). У старых запросов
    metadata стирается, но остаётся отпечаток. Исторические общие запросы с двумя
    результатами остаются читаемыми.
    """

    __tablename__ = "analytics_requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshots.id", ondelete="SET NULL"))  # кадр чистится раньше
    at: Mapped[datetime] = mapped_column(UTCDateTime)  # когда отправлен
    trigger: Mapped[str] = mapped_column(String(10))  # schedule | manual
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)  # когда снят кадр
    image_sha256: Mapped[str] = mapped_column(String(64))
    input_sha256: Mapped[str] = mapped_column(String(64))
    catalog_version: Mapped[str] = mapped_column(String(64))
    plan_revision_id: Mapped[str | None] = mapped_column(String(100))  # None — план не отправлен
    plan_note: Mapped[str | None] = mapped_column(Text)  # почему план не отправлен (для администратора)
    notes: Mapped[list] = mapped_column(JSON, default=list)  # что ещё не попало в запрос: технику нет в справочнике и т. п.
    metadata_json: Mapped[str | None] = mapped_column(Text)

    results: Mapped[list["AnalyticsResult"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AnalyticsResult.service"
    )

    __table_args__ = (
        Index("ix_analytics_requests_camera_at", "camera_id", "at"),
        Index("ix_analytics_requests_site_at", "site_id", "at"),
    )


class AnalyticsResult(Base):
    """Ответ одного сервиса на запрос. state: pending — ждём; done — анализ выполнен (result — ответ сервиса целиком);
    error — сервис отказал или не ответил (error_code, error — почему); unknown — неизвестно, выполнен ли анализ."""

    __tablename__ = "analytics_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("analytics_requests.id", ondelete="CASCADE"), index=True)
    service: Mapped[str] = mapped_column(String(16))  # deterministic | vlm_llm
    state: Mapped[str] = mapped_column(String(10), default="pending")
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    elapsed_ms: Mapped[int | None]
    http_status: Mapped[int | None]
    analysis_id: Mapped[str | None] = mapped_column(String(100))
    outcome: Mapped[str | None] = mapped_column(String(24))  # current_work.status: assessed, insufficient_evidence…
    result: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None]

    __table_args__ = (UniqueConstraint("request_id", "service", name="uq_analytics_results_request_service"),)


class Detection(Base):
    """Единица техники на снимке. Рамка — в процентах от кадра 16:9, начало координат слева сверху."""

    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id", ondelete="CASCADE"), index=True)
    equipment_type: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float]
    x: Mapped[float]
    y: Mapped[float]
    w: Mapped[float]
    h: Mapped[float]
    moving: Mapped[bool | None]  # сдвинулась ли техника относительно предыдущего кадра этой камеры


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    number: Mapped[int] = mapped_column(unique=True)  # сквозной номер для журнала: ОТК-26-0137
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    zone_id: Mapped[str] = mapped_column(ForeignKey("zones.id"))
    stage_id: Mapped[str | None] = mapped_column(ForeignKey("stages.id"))
    camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.id"))

    kind: Mapped[str] = mapped_column(String(16))  # missing | count_below | unexpected | idle | camera_offline
    severity: Mapped[str] = mapped_column(String(8))  # high | medium | low
    status: Mapped[str] = mapped_column(String(16), default="new")
    equipment_type: Mapped[str | None] = mapped_column(String(20))
    expected: Mapped[int | None]
    observed: Mapped[int | None]

    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    consequence: Mapped[str] = mapped_column(Text, default="")
    advice: Mapped[str] = mapped_column(Text, default="")
    prescription_no: Mapped[str | None] = mapped_column(String(20))
    prescription_due: Mapped[date | None] = mapped_column(Date)  # срок устранения по предписанию

    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # условие перестало наблюдаться
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    evidence: Mapped[list[Snapshot]] = relationship(secondary=alert_evidence, lazy="selectin", order_by="Snapshot.taken_at")
    events: Mapped[list["AlertEvent"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AlertEvent.at, AlertEvent.id"
    )

    __table_args__ = (Index("ix_alerts_site_status", "site_id", "status"),)


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    who: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(16))  # статус, установленный этим событием


class AuditEvent(Base):
    """Журнал действий: кто, когда и что сделал в системе (добавил камеру, сменил пароль, закрыл отклонение…)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # кто — копией, а не ссылкой: запись должна пережить удаление пользователя
    actor_id: Mapped[str | None] = mapped_column(String(40))
    actor_login: Mapped[str] = mapped_column(String(120), default="")
    actor_name: Mapped[str] = mapped_column(String(160), default="")
    actor_role: Mapped[str] = mapped_column(String(20), default="")
    action: Mapped[str] = mapped_column(String(40), index=True)  # camera.delete, user.password, login.failed…
    entity_type: Mapped[str] = mapped_column(String(20), default="")  # camera | site | user | rule | alert | stage | zone
    entity_id: Mapped[str | None] = mapped_column(String(40))
    entity_name: Mapped[str] = mapped_column(String(300), default="")
    summary: Mapped[str] = mapped_column(Text)  # по-человечески: «Удалил камеру «Камера 2 — въезд» (Школа на 550 мест)»
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # что именно поменялось: {"поле": [было, стало]}
    ip: Mapped[str] = mapped_column(String(64), default="")


OPEN_STATUSES = ("new", "acknowledged", "confirmed", "prescribed")
