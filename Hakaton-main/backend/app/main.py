"""СтройКонтроль API: точка входа."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from app.api import (
    admin,
    alerts,
    analyze,
    audit,
    auth,
    cameras,
    catalog,
    equipment,
    ingest,
    photo_analyses,
    plan,
    reports,
    snapshots,
    spider,
    tracks,
    work,
)
from app.api import video as video_api
from app.config import ASSETS_DIR, get_settings
from app.db import SessionLocal, engine, utcnow
from app.models import SpiderImport
from app.schemas import ApiModel
from app.seed import prepare_database
from app.services import video
from app.services.analysis import LocalAnalyzer, detectable_types, get_analyzer, provider_name
from app.services.analytics.runner import get_analytics
from app.services.equipment_visits import mark_stale_visits
from app.services.pipeline import get_pipeline
from app.services.realtime import LiveTracking
from app.services.spider import refresh_sources
from app.services.tracks import get_relay

VERSION = "0.10.0"
settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stroykontrol")




async def _sweep_equipment_visits() -> None:
    """Persist timeout losses even while no browser or tracker connection is open."""
    while True:
        try:
            async with SessionLocal() as session:
                await mark_stale_visits(session, utcnow())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Не удалось проверить устаревшие наблюдения техники")
        await asyncio.sleep(5)


async def _refresh_spider_sources() -> None:
    """Refresh all already associated sites once per configured interval with one shared source fetch."""
    interval = settings.camera_stage_monitor_refresh_seconds
    while True:
        try:
            async with SessionLocal() as session:
                site_ids = list(
                    await session.scalars(
                        select(SpiderImport.site_id).where(SpiderImport.status == "succeeded").distinct()
                    )
                )
            if site_ids:
                await refresh_sources(SessionLocal, site_ids)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Не удалось обновить источник Camera Stage Monitor")
        await asyncio.sleep(interval)
@asynccontextmanager
async def lifespan(_: FastAPI):
    if problems := settings.insecure_defaults():
        raise RuntimeError("Боевой запуск (SK_DEMO_MODE=false) с небезопасными настройками:\n- " + "\n- ".join(problems))
    settings.frames_dir.mkdir(parents=True, exist_ok=True)
    await prepare_database()
    background_tasks = [asyncio.create_task(_sweep_equipment_visits())]
    if settings.camera_stage_monitor_refresh_seconds > 0:
        background_tasks.append(asyncio.create_task(_refresh_spider_sources()))
    analyzer, relay = get_analyzer(), get_relay()  # своя модель загружается здесь — до первого кадра
    log.info(
        "Анализ кадров: %s, кадр с камеры раз в %.0f с, сверка раз в %d с",
        provider_name(),
        settings.frame_interval_s,
        settings.check_interval_s,
    )
    if isinstance(analyzer, LocalAnalyzer) and not settings.tracker_url:
        # рамки в реальном времени — своей моделью (внешний сервис разметки, если задан, важнее); до запуска конвейера:
        # он сообщает, какие камеры включены
        relay.local = LiveTracking(analyzer.detector, relay.deliver, relay.watched)
    feeds, pipeline = video.DemoFeeds(), get_pipeline()
    if settings.video_enabled:
        if settings.demo_mode:
            feeds.start()  # явный стендовый режим: демо-ролики → шлюз
        pipeline.start()  # потоки камер в шлюзе, кадры на анализ, сверка объектов
    relay.start()  # рамки в реальном времени: своя модель или внешний сервис разметки (SK_TRACKER_URL)
    analytics = get_analytics()
    await analytics.start()  # какая работа идёт на кадре — если подключены сервисы аналитики (DETERMINISTIC_SERVICE_URL…)
    yield
    await analytics.stop()
    for task in background_tasks:
        task.cancel()
    for task in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await relay.stop()
    await pipeline.stop()
    await feeds.stop()
    await engine.dispose()


app = FastAPI(
    title="СтройКонтроль API",
    version=VERSION,
    description="Мониторинг строительных площадок по видео с камер: кадры → техника → сверка с графиком → отклонения.",
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["*"], allow_headers=["*"])


class VideoOut(ApiModel):
    enabled: bool
    webrtc_url: str  # браузер смотрит поток камеры по адресу {webrtcUrl}/{streamPath}/whep
    frame_interval_s: float
    check_interval_s: int


class TrackerOut(ApiModel):
    """Рамки в реальном времени (WebSocket /api/tracks): своя модель по видео камер или внешний сервис разметки."""

    enabled: bool
    connected: bool  # рамки могут идти: модель загружена / к сервису есть подключение
    video_delay_ms: int  # на столько придержать видео, чтобы рамки совпадали с картинкой
    last_message_at: str | None  # когда пришло последнее сообщение — видно, идут ли рамки вообще


class DemoFeedOut(ApiModel):
    """Демо-ролик как RTSP-адрес — для быстрой настройки в форме «Добавить камеру»."""

    clip: str
    title: str
    host: str
    port: int
    path: str


class MetaOut(ApiModel):
    version: str
    demo_mode: bool
    analysis_provider: str
    # какую технику анализ кадров умеет находить (своя модель — не всю): остальную в правилах проверяют на месте
    detectable_equipment: list[str]
    timezone: str
    server_time: str
    database: str
    auth_mode: str  # local | keycloak
    keycloak: dict | None  # {url, realm, clientId} — когда включён Keycloak
    video: VideoOut
    tracker: TrackerOut
    demo_feeds: list[DemoFeedOut]


api = APIRouter(prefix="/api")


@api.get("/meta", response_model=MetaOut, tags=["Служебное"], summary="Состояние сервера и режимы работы")
async def meta() -> MetaOut:
    relay = get_relay()
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
    keycloak = None
    if settings.keycloak_issuer:
        keycloak = {"url": settings.keycloak_url, "realm": settings.keycloak_realm, "clientId": settings.keycloak_client_id}
    feeds = []
    if settings.demo_mode:
        for clip in video.available_clips():
            addr = video.demo_feed_address(clip)
            feeds.append(
                DemoFeedOut(clip=clip, title=video.CLIP_TITLES.get(clip, clip), host=addr.host, port=addr.port, path=addr.path)
            )
    return MetaOut(
        version=VERSION,
        demo_mode=settings.demo_mode,
        analysis_provider=provider_name(),
        detectable_equipment=sorted(detectable_types()),
        timezone=settings.timezone,
        server_time=utcnow().isoformat(),
        database="sqlite" if settings.is_sqlite else "postgresql",
        auth_mode=settings.auth_mode,
        keycloak=keycloak,
        video=VideoOut(
            enabled=settings.video_enabled,
            webrtc_url=settings.video_webrtc_url.rstrip("/"),
            frame_interval_s=settings.frame_interval_s,
            check_interval_s=settings.check_interval_s,
        ),
        tracker=TrackerOut(
            enabled=relay.enabled,
            connected=relay.connected,
            video_delay_ms=settings.tracker_video_delay_ms,
            last_message_at=relay.last_message_at.isoformat() if relay.last_message_at else None,
        ),
        demo_feeds=feeds,
    )


for module in (
    auth, catalog, cameras, snapshots, photo_analyses, alerts, analyze, reports, ingest, equipment, spider,
    video_api, tracks, work, audit, admin, plan,
):
    api.include_router(module.router)
app.include_router(api)

if settings.demo_mode:
    app.mount("/media/seed", StaticFiles(directory=ASSETS_DIR / "seed"), name="seed-media")
app.mount("/media/frames", StaticFiles(directory=settings.frames_dir, check_dir=False), name="frames")
