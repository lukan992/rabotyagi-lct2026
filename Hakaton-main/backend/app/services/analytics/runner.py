"""Отправка кадров сервисам аналитики и приём ответов — по расписанию и по кнопке.

Раз в analytics_interval_min минут по каждой камере рабочей зоны свежий кадр (новый с прошлой отправки) уходит
подключённым сервисам одновременно. Для каждой фактической отправки сохраняется отдельный request_id, чтобы входы
v1 и v2 и их отпечатки не смешивались. Камеры и объекты — по очереди: сервис по изображению думает минутами и стоит
денег. Пока сервисы думают, соединение с базой не держим. Ответы и отказы хранятся в analytics_results; оба ответа
показываются рядом, «победителя» не выбираем (раздел 12).
"""

import asyncio
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import AnalyticsRequest, AnalyticsResult, Camera, Site, Snapshot, Zone, new_id
from app.services.analytics.catalog import CatalogCache, CatalogError
from app.services.analytics.client import Reply, ServiceClient
from app.services.analytics.images import ImageProblem, load_image, photo_path
from app.services.analytics.request import BuiltRequest, RequestProblem, build_request, for_service, spider_context
from app.services.analytics.result import check_result
from app.services.engine import FRESHNESS
from app.services.workhours import working_now

log = logging.getLogger("stroykontrol.analytics")

STARTUP_DELAY_S = 30.0  # после запуска сервера — подождать, пока сверка сохранит свежие кадры
ROUND_S = 60.0
CLEANUP_S = 3600.0


async def work_cameras(session: AsyncSession, site_id: str | None = None) -> list[Camera]:
    """Камеры рабочих зон: только их кадры уходят сервисам."""
    query = (
        select(Camera)
        .join(Zone, Camera.zone_id == Zone.id)
        .join(Site, Camera.site_id == Site.id)
        .where(Camera.enabled, Camera.deleted_at.is_(None), Zone.kind == "work")
        .order_by(Site.position, Camera.position)
    )
    if site_id:
        query = query.where(Camera.site_id == site_id)
    return list(await session.scalars(query))


async def fresh_snapshot(session: AsyncSession, camera_id: str, now: datetime) -> Snapshot | None:
    return await session.scalar(
        select(Snapshot)
        .where(Snapshot.camera_id == camera_id, Snapshot.taken_at <= now, Snapshot.taken_at >= now - FRESHNESS)
        .order_by(Snapshot.taken_at.desc())
        .limit(1)
    )


class Analytics:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        settings = get_settings()
        self.clients = {
            name: ServiceClient(name, url, settings.analytics_service_token, settings.analytics_http_timeout_seconds, transport)
            for name, url in settings.analytics_services.items()
        }
        # сохранённый справочник — только при подключённых сервисах: без них выбирать вид работ не из чего
        self.catalog = CatalogCache(self._fetch_catalog, settings.data_dir / "analytics-catalog.json" if self.clients else None)
        self.problems: dict[str, tuple[datetime, str]] = {}  # объект → почему последний запрос не ушёл
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._llm_slots = asyncio.Semaphore(3)
        self.llm_waiting = 0
        self._manual: dict[str, asyncio.Task] = {}
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.clients)

    def running(self, site_id: str) -> bool:
        task = self._manual.get(site_id)
        return bool(task and not task.done()) or self._locks[site_id].locked()

    async def _fetch_catalog(self) -> dict:
        if not self.clients:
            raise CatalogError("сервисы аналитики не подключены")
        errors = []
        for client in self.clients.values():  # справочник у сервисов общий: берём у первого ответившего
            try:
                return await client.get_json("/v1/catalog")
            except CatalogError as exc:
                errors.append(str(exc))
        raise CatalogError("; ".join(errors))

    # ---------- один кадр ----------
    async def analyze_camera(
        self, camera_id: str, *, trigger: str, only_new: bool = True, services: list[str] | None = None
    ) -> list[AnalyticsRequest]:
        """Отправить свежий кадр камеры сервисам и сохранить отдельный запрос для каждой фактической отправки."""
        names = [name for name in self.clients if services is None or name in services]
        async with SessionLocal() as session:
            camera = await session.get(Camera, camera_id)
        if camera is None or not names:
            return []
        async with self._locks[camera.site_id]:
            built = await self._prepare(camera_id, trigger=trigger, only_new=only_new, services=names)
        return await self.complete(built)

    async def prepare_photo(
        self, snapshot_id: str, *, services: list[str] | None = None
    ) -> dict[str, BuiltRequest]:
        """Persist pending per-service photo requests before their background network execution."""
        names = [name for name in self.clients if services is None or name in services]
        if not names:
            return {}
        async with SessionLocal() as session:
            snapshot = await session.get(Snapshot, snapshot_id)
            site = await session.get(Site, snapshot.site_id) if snapshot else None
            if snapshot is None or site is None or snapshot.source != "photo" or snapshot.camera_id is not None:
                return {}
            async with self._locks[site.id]:
                preparation_notes: list[str] = []
                prepared = await self._prepare_snapshot(
                    session,
                    site=site,
                    snapshot=snapshot,
                    camera=None,
                    trigger="photo",
                    services=names,
                    use_spider=bool(getattr(snapshot, "photo_use_spider", False)),
                    local_notes=preparation_notes,
                )
                if not prepared:
                    message = self.problems.get(site.id, (utcnow(), "не удалось подготовить анализ фото"))[1]
                    await self._record_photo_problem(
                        session, site=site, snapshot=snapshot, services=names, message=message, notes=preparation_notes
                    )
                return prepared

    async def _record_photo_problem(
        self, session: AsyncSession, *, site: Site, snapshot: Snapshot, services: list[str], message: str, notes: list[str]
    ) -> None:
        """A preparation failure is a durable per-service answer, never an invisible empty poll response."""
        try:
            image_sha256 = load_image(snapshot.image_url).sha256
        except ImageProblem:
            image_sha256 = "0" * 64
        for service in services:
            session.add(
                AnalyticsRequest(
                    id=new_id("fa"),
                    site_id=site.id,
                    camera_id=None,
                    snapshot_id=snapshot.id,
                    at=utcnow(),
                    trigger="photo",
                    observed_at=snapshot.taken_at,
                    image_sha256=image_sha256,
                    input_sha256="0" * 64,
                    catalog_version="unavailable",
                    plan_revision_id=None,
                    plan_note=message,
                    notes=list(notes),
                    metadata_json=None,
                    results=[
                        AnalyticsResult(
                            service=service,
                            state="error",
                            finished_at=utcnow(),
                            error_code="preparation_failure",
                            error=message,
                            retryable=False,
                        )
                    ],
                )
            )
        await session.commit()

    async def complete(self, built: dict[str, BuiltRequest]) -> list[AnalyticsRequest]:
        """Execute already-persisted requests. Pending rows stay inspectable across a restart."""
        if not built:
            return []
        replies = await asyncio.gather(*(self._ask(name, request) for name, request in built.items()))
        async with SessionLocal() as session:
            rows = {
                row.request_id: row
                for row in await session.scalars(
                    select(AnalyticsResult).where(
                        AnalyticsResult.request_id.in_([request.request_id for request in built.values()])
                    )
                )
            }
            for request, (reply, elapsed_ms) in zip(built.values(), replies, strict=True):
                _apply(rows[request.request_id], reply, elapsed_ms)
            await session.commit()
            return [
                await session.get(AnalyticsRequest, request.request_id, populate_existing=True)
                for request in built.values()
            ]


    async def _prepare(
        self, camera_id: str, *, trigger: str, only_new: bool, services: list[str]
    ) -> dict[str, BuiltRequest]:
        async with SessionLocal() as session:
            camera = await session.get(Camera, camera_id)
            site = await session.get(Site, camera.site_id) if camera else None
            if site is None or not camera.enabled or camera.deleted_at is not None or camera.zone.kind != "work":
                return {}
            now = utcnow()
            snapshot = await fresh_snapshot(session, camera.id, now)
            if snapshot is None:
                return {}
            return await self._prepare_snapshot(
                session,
                site=site,
                snapshot=snapshot,
                camera=camera,
                trigger=trigger,
                services=services,
                use_spider=bool(getattr(camera, "spider_enabled", False)),
                only_new=only_new,
                now=now,
            )

    async def _prepare_snapshot(
        self,
        session: AsyncSession,
        *,
        site: Site,
        snapshot: Snapshot,
        camera: Camera | None,
        trigger: str,
        services: list[str],
        use_spider: bool,
        only_new: bool = False,
        now: datetime | None = None,
        local_notes: list[str] | None = None,
    ) -> dict[str, BuiltRequest]:
        now = now or utcnow()
        names = list(services)
        if only_new and camera is not None:
            sent = {}
            for service, snapshot_id in await session.execute(
                select(AnalyticsResult.service, AnalyticsRequest.snapshot_id)
                .join(AnalyticsRequest, AnalyticsRequest.id == AnalyticsResult.request_id)
                .where(AnalyticsRequest.camera_id == camera.id, AnalyticsResult.service.in_(names))
                .order_by(AnalyticsRequest.at.desc(), AnalyticsRequest.id.desc())
            ):
                sent.setdefault(service, snapshot_id)
            names = [service for service in names if sent.get(service) != snapshot.id]
        if not names:
            return {}
        spider = None
        if use_spider:
            try:
                spider = await spider_context(session, site.id, now=now)
            except RequestProblem as exc:
                source = camera.name if camera is not None else f"фото {snapshot.id}"
                self.problems[site.id] = (now, f"{source}: {exc}")
                log.warning("Контекст Spider для %s не подготовлен: %s", source, exc)
                return {}
        spider_unavailable = use_spider and spider is None
        if spider_unavailable and local_notes is not None:
            local_notes.append("spider_context_unavailable")
        try:
            catalog = await self.catalog.get()
            base = await build_request(
                session, request_id=new_id("fa"), site=site, camera=camera, snapshot=snapshot, catalog=catalog
            )
        except (CatalogError, RequestProblem, ImageProblem) as exc:
            source = camera.name if camera is not None else f"фото {snapshot.id}"
            self.problems[site.id] = (now, f"{source}: {exc}")
            log.warning("Снимок %s не отправлен сервисам аналитики: %s", snapshot.id, exc)
            return {}
        if spider_unavailable:
            base.notes.append("spider_context_unavailable")
        built = {}
        for service in names:
            try:
                built[service] = for_service(base, request_id=new_id("fa"), service=service, spider=spider)
            except RequestProblem as exc:
                source = camera.name if camera is not None else f"фото {snapshot.id}"
                self.problems[site.id] = (now, f"{source}: {exc}")
                log.warning("Ресурсный вход для %s не подготовлен: %s", source, exc)
                return {}
        self.problems.pop(site.id, None)
        for service, request in built.items():
            session.add(
                AnalyticsRequest(
                    id=request.request_id,
                    site_id=site.id,
                    camera_id=camera.id if camera is not None else None,
                    snapshot_id=snapshot.id,
                    at=now,
                    trigger=trigger,
                    observed_at=snapshot.taken_at,
                    image_sha256=request.image.sha256,
                    input_sha256=request.input_sha256,
                    catalog_version=catalog.version,
                    plan_revision_id=request.plan_revision_id,
                    plan_note=request.plan_note,
                    notes=request.notes,
                    metadata_json=request.body.decode("utf-8"),
                    results=[AnalyticsResult(service=service, state="pending")],
                )
            )
        await session.commit()
        return built

    async def _ask(self, service: str, built: BuiltRequest) -> tuple[Reply, int]:
        started = time.perf_counter()
        try:
            if service == "vlm_llm":
                waiting = self._llm_slots.locked()
                if waiting:
                    self.llm_waiting += 1
                try:
                    await self._llm_slots.acquire()
                finally:
                    if waiting:
                        self.llm_waiting -= 1
                try:
                    reply = await self.clients[service].analyze(
                        request_id=built.request_id,
                        site_id=built.metadata["site_id"],
                        schema_version=built.metadata["schema_version"],
                        metadata=built.body,
                        image=built.image.data,
                        media_type=built.image.media_type,
                    )
                finally:
                    self._llm_slots.release()
            else:
                reply = await self.clients[service].analyze(
                    request_id=built.request_id,
                    site_id=built.metadata["site_id"],
                    schema_version=built.metadata["schema_version"],
                    metadata=built.body,
                    image=built.image.data,
                    media_type=built.image.media_type,
                )
        except Exception as exc:  # noqa: BLE001 — сбой клиента не должен оставить запрос «в ожидании» навсегда
            log.exception("Сбой при обращении к сервису %s", service)
            reply = Reply("unknown", code="client_failure", message=f"сбой на нашей стороне: {type(exc).__name__}")
        if reply.state == "done" and (problem := check_result(reply.result, service=service, metadata=built.metadata,
                                                              input_sha256=built.input_sha256)):  # fmt: skip
            log.warning("Ответ сервиса %s на %s не принят: %s", service, built.request_id, problem)
            reply = Reply("error", reply.http_status, result=reply.result, code="invalid_result", message=problem)
        return reply, int((time.perf_counter() - started) * 1000)

    # ---------- объект целиком ----------
    async def analyze_site(self, site_id: str, *, trigger: str, only_new: bool = False) -> list[AnalyticsRequest]:
        async with SessionLocal() as session:
            camera_ids = [c.id for c in await work_cameras(session, site_id)]
        completed = await asyncio.gather(
            *(self.analyze_camera(camera_id, trigger=trigger, only_new=only_new) for camera_id in camera_ids)
        )
        return [request for requests in completed for request in requests]

    def start_site(self, site_id: str) -> bool:
        """«Определить сейчас»: все камеры рабочих зон объекта в фоне. False — уже идёт."""
        if self.running(site_id):
            return False
        self._manual[site_id] = asyncio.create_task(self.analyze_site(site_id, trigger="manual"), name=f"analytics-{site_id}")
        return True

    # ---------- по расписанию ----------
    async def start(self) -> None:
        async with SessionLocal() as session:
            await interrupt_pending(session)
        if self.clients and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="analytics")

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._manual.values()) if t and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = None
        for client in self.clients.values():
            await client.close()

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        await asyncio.sleep(STARTUP_DELAY_S)
        cleaned = -math.inf
        while True:
            try:
                await self.round()
                if loop.time() - cleaned >= CLEANUP_S:
                    cleaned = loop.time()
                    async with SessionLocal() as session:
                        await cleanup(session)
            except Exception:  # noqa: BLE001 — сбой одного круга не останавливает расписание
                log.exception("Сбой планового круга аналитики")
            await asyncio.sleep(ROUND_S)

    async def round(self) -> None:
        """Отправить кадры камер, которым пора: у каждого сервиса свой интервал (по снимку — реже: он платный),
        и только в рабочее время объекта — ночью техники нет, отправлять нечего."""
        settings = get_settings()
        async with SessionLocal() as session:
            cameras = await work_cameras(session)
            sites = {site.id: site for site in await session.scalars(select(Site))}
            last = {
                (camera_id, service): at
                for camera_id, service, at in await session.execute(
                    select(AnalyticsRequest.camera_id, AnalyticsResult.service, func.max(AnalyticsRequest.at))
                    .join(AnalyticsResult, AnalyticsResult.request_id == AnalyticsRequest.id)
                    .where(AnalyticsRequest.camera_id.is_not(None))
                    .group_by(AnalyticsRequest.camera_id, AnalyticsResult.service)
                )
            }
        now = utcnow()
        every = {"deterministic": settings.analytics_interval_min, "vlm_llm": settings.analytics_vlm_interval_min}
        for camera in cameras:
            if not working_now(sites[camera.site_id], now):
                continue
            due = [
                name
                for name in self.clients
                if (camera.id, name) not in last or last[(camera.id, name)] <= now - timedelta(minutes=every[name])
            ]
            if due:
                await self.analyze_camera(camera.id, trigger="schedule", services=due)


def _apply(row: AnalyticsResult, reply: Reply, elapsed_ms: int) -> None:
    row.state, row.finished_at, row.elapsed_ms, row.http_status = reply.state, utcnow(), elapsed_ms, reply.http_status
    row.result, row.error_code, row.error, row.retryable = reply.result, reply.code, reply.message, reply.retryable
    if reply.state == "done" and reply.result:
        row.analysis_id = str(reply.result.get("analysis_id") or "")[:100] or None
        row.outcome = reply.result["current_work"]["status"]


async def interrupt_pending(session: AsyncSession) -> None:
    """После перезапуска сервера ответы, которых ждали, уже не придут: выполнен ли анализ — неизвестно."""
    await session.execute(
        update(AnalyticsResult)
        .where(AnalyticsResult.state == "pending")
        .values(state="unknown", error_code="interrupted", error="сервер перезапустился, пока ждал ответ", finished_at=utcnow())
    )
    await session.commit()

async def cleanup(session: AsyncSession) -> None:
    """Delete expired requests/uploads; retain camera metadata only for each camera/service pair."""
    settings = get_settings()
    cutoff = utcnow() - timedelta(days=settings.keep_usage_days)
    expired_photos = list(
        await session.scalars(
            select(Snapshot).where(Snapshot.source == "photo", Snapshot.taken_at < cutoff)
        )
    )
    paths = [photo_path(snapshot.id) for snapshot in expired_photos]
    if expired_photos:
        await session.execute(delete(Snapshot).where(Snapshot.id.in_([snapshot.id for snapshot in expired_photos])))
    await session.execute(delete(AnalyticsRequest).where(AnalyticsRequest.at < cutoff))
    ranked = (
        select(
            AnalyticsRequest.id,
            func.row_number()
            .over(
                partition_by=(AnalyticsRequest.camera_id, AnalyticsResult.service),
                order_by=(AnalyticsRequest.at.desc(), AnalyticsRequest.id.desc()),
            )
            .label("rank"),
        )
        .join(AnalyticsResult, AnalyticsResult.request_id == AnalyticsRequest.id)
        .where(AnalyticsRequest.camera_id.is_not(None))
        .subquery()
    )
    keep = select(ranked.c.id).where(ranked.c.rank == 1)
    await session.execute(
        update(AnalyticsRequest)
        .where(
            AnalyticsRequest.camera_id.is_not(None),
            AnalyticsRequest.metadata_json.is_not(None),
            AnalyticsRequest.id.not_in(keep),
        )
        .values(metadata_json=None)
    )
    await session.commit()
    for path in paths:
        if path is not None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("Не удалось удалить просроченное загруженное фото %s", path)


_analytics: Analytics | None = None


def get_analytics() -> Analytics:
    global _analytics
    if _analytics is None:
        _analytics = Analytics()
    return _analytics
