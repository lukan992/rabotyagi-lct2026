"""Конвейер живого видео: кадр каждые 2 секунды → анализ → сверка объекта с планом раз в минуту.

• С каждой включённой камеры кадры берутся прямо из её видеопотока в шлюзе (ffmpeg, fps = 1/frame_interval_s).
  Каждый кадр разбирает анализатор: своя модель в сервере, демо-заглушка или внешний сервис — по настройкам.
  Анализ не успевает за кадрами — берётся самый свежий кадр, старые отбрасываются: очередь не копится.
• Результаты держим в памяти (последняя минута по каждой камере) — их видно в интерфейсе поверх видео.
• Раз в check_interval_s объект сверяется с правилом этапа. В базу пишется по одному кадру на камеру
  (самый частый за минуту набор техники — одиночная ошибка распознавания не делает погоды): это доказательства
  для предупреждений и история для поиска простоя.
"""

import asyncio
import logging
import shutil
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import Camera, Site
from app.services import video
from app.services.analysis import AnalysisError, AnalysisResult, get_analyzer
from app.services.engine import check_site, cleanup_frames
from app.services.tracks import get_relay

log = logging.getLogger("stroykontrol.pipeline")
settings = get_settings()

FRAME_SIZE = "1280:720"
_JPEG_START, _JPEG_END = b"\xff\xd8", b"\xff\xd9"


@dataclass
class LiveFrame:
    at: datetime
    jpeg: bytes
    result: AnalysisResult

    @property
    def counts(self) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(Counter(d.type for d in self.result.detections).items()))


@dataclass
class CameraLive:
    """Что сейчас видит камера: связь, последний разобранный кадр, кадры за последнюю минуту."""

    camera_id: str
    online: bool = False
    error: str | None = None
    received_at: datetime | None = None  # последний полученный кадр (даже если ещё не разобран)
    frame: LiveFrame | None = None
    history: deque[LiveFrame] = field(default_factory=lambda: deque(maxlen=90))

    def recent(self, window: timedelta, now: datetime) -> list[LiveFrame]:
        return [f for f in self.history if now - f.at <= window]


def fresh_limit() -> timedelta:
    """Кадр старше — камера сейчас ничего не показывает: такой кадр не годится для сверки."""
    return timedelta(seconds=max(settings.frame_interval_s * 5, 15))


def representative(frames: list[LiveFrame]) -> LiveFrame | None:
    """Самый свежий кадр с самым частым за окно набором техники: одиночный сбой распознавания не ломает сверку."""
    analyzed = [f for f in frames if f.result.supported]
    if not analyzed:
        return frames[-1] if frames else None
    typical, _ = Counter(f.counts for f in analyzed).most_common(1)[0]
    return next(f for f in reversed(analyzed) if f.counts == typical)


def _friendly(stderr: str) -> str:
    text = stderr.lower()
    if "404" in text or "not found" in text:
        return "Шлюз не получает видео с камеры: проверьте адрес потока и что камера включена"
    if "401" in text or "unauthorized" in text:
        return "Шлюз не пустил за видео (служебная учётка)"
    if "connection refused" in text:
        return "Шлюз видео не запущен"
    if "timed out" in text or "timeout" in text:
        return "Видео с камеры не приходит: истекло время ожидания"
    return "Видео с камеры прервалось"


class Pipeline:
    def __init__(self) -> None:
        self.live: dict[str, CameraLive] = {}
        self._samplers: dict[str, asyncio.Task] = {}
        self._checker: asyncio.Task | None = None
        self._syncer: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._pending: set[str] = set()
        self._ffmpeg = shutil.which("ffmpeg")

    # ---------- жизненный цикл ----------
    def start(self) -> None:
        if not self._ffmpeg:
            log.warning("Нет ffmpeg — кадры из видео на анализ не берутся")
        self._syncer = asyncio.create_task(self._sync_loop(), name="video-sync")
        if settings.check_interval_s > 0:
            self._checker = asyncio.create_task(self._check_loop(), name="site-checker")

    async def stop(self) -> None:
        tasks = [t for t in (self._checker, self._syncer, *self._samplers.values()) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._samplers.clear()

    def state(self, camera_id: str) -> CameraLive | None:
        return self.live.get(camera_id)

    def request_check(self, site_ids: set[str]) -> None:
        """Сверить объекты вне очереди (например, после правки правила), не дожидаясь минуты."""
        self._pending |= site_ids
        self._wake.set()

    # ---------- потоки в шлюзе и чтение кадров ----------
    async def sync(self) -> None:
        """Привести шлюз и чтение кадров в соответствие с камерами в базе."""
        async with SessionLocal() as session:
            cameras = list(await session.scalars(select(Camera).where(Camera.deleted_at.is_(None))))
        wanted = {c.id: c for c in cameras if c.enabled}
        if settings.video_enabled:  # без шлюза (тесты) его не трогаем — даже если на этом компьютере он запущен
            await self._sync_gateway(wanted)

        if (tracking := get_relay().local) is not None:  # своя модель: чьё видео можно разбирать в реальном времени
            tracking.set_cameras(wanted)
        analyzer = get_analyzer()
        for camera in wanted.values():
            if hasattr(analyzer, "bind_camera"):  # демо-анализатору говорим, какой ролик показывает камера
                analyzer.bind_camera(camera.id, video.demo_clip_of(camera.path))
            if camera.id not in self._samplers and self._ffmpeg and settings.video_enabled:
                self._samplers[camera.id] = asyncio.create_task(self._sample(camera.id), name=f"sampler-{camera.id}")
        for camera_id in list(self._samplers):
            if camera_id not in wanted:
                self._samplers.pop(camera_id).cancel()
                self.live.pop(camera_id, None)

    async def _sync_gateway(self, wanted: dict[str, Camera]) -> None:
        gateway = video.get_gateway()
        try:
            configured = await gateway.configured()
            for camera in wanted.values():
                name, source = video.camera_path(camera.id), camera_source_url(camera)
                if configured.get(name) != source:  # без изменений не трогаем: шлюз переподключился бы к камере
                    await gateway.set_source(name, source)
            now = asyncio.get_running_loop().time()
            for name in configured:
                stale_camera = name.startswith(video.CAMERA_PREFIX) and video.camera_id_from_path(name) not in wanted
                stale_probe = name.startswith(video.PROBE_PREFIX) and now - video.probes.get(name, 0) > video.PROBE_TTL_S
                if stale_camera or stale_probe:
                    await gateway.remove(name)
                    video.probes.pop(name, None)
        except video.GatewayError as exc:
            log.warning("%s — повторим позже", exc)

    async def _sync_loop(self) -> None:
        while True:  # шлюз мог перезапуститься и забыть потоки — сверяемся раз в 30 секунд
            try:
                await self.sync()
            except Exception:
                log.exception("Сбой синхронизации видео")
            await asyncio.sleep(30)

    async def _sample(self, camera_id: str) -> None:
        live = self.live.setdefault(camera_id, CameraLive(camera_id))
        delay = 2.0
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self._read_stream(camera_id, live)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Сбой чтения кадров камеры %s", camera_id)
            live.online = False
            if asyncio.get_running_loop().time() - started > 60:
                delay = 2.0
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _read_stream(self, camera_id: str, live: CameraLive) -> None:
        interval = settings.frame_interval_s
        command = [
            self._ffmpeg or "ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
            "-timeout", str(int(settings.camera_timeout_s * 1_000_000)),
            "-i", video.internal_rtsp_url(video.camera_path(camera_id)), "-an",
            "-vf", f"fps=1/{interval},scale={FRAME_SIZE}:force_original_aspect_ratio=decrease,pad={FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "5", "pipe:1",
        ]  # fmt: skip
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        latest: asyncio.Queue[tuple[datetime, bytes]] = asyncio.Queue(maxsize=1)
        analyzer_task = asyncio.create_task(self._analyze(camera_id, live, latest))
        stderr_tail = bytearray()

        async def drain_stderr() -> None:
            while chunk := await process.stderr.read(4096):
                stderr_tail.extend(chunk)
                del stderr_tail[:-2000]

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            buffer = b""
            while chunk := await process.stdout.read(65536):
                buffer += chunk
                while (start := buffer.find(_JPEG_START)) >= 0 and (end := buffer.find(_JPEG_END, start + 2)) >= 0:
                    jpeg, buffer = buffer[start : end + 2], buffer[end + 2 :]
                    live.online, live.error, live.received_at = True, None, utcnow()
                    if latest.full():  # анализ не успевает — старый кадр выбрасываем, берём свежий
                        latest.get_nowait()
                    latest.put_nowait((live.received_at, jpeg))
                if len(buffer) > settings.max_frame_bytes:
                    buffer = b""
        finally:
            analyzer_task.cancel()
            if process.returncode is None:
                process.kill()
            await process.wait()
            await asyncio.gather(analyzer_task, stderr_task, return_exceptions=True)
            live.online = False
            text = video.redact(stderr_tail.decode(errors="ignore")).strip()
            live.error = _friendly(text)
            log.info("Камера %s: видео прервалось (%s)", camera_id, text.splitlines()[-1] if text else "поток закончился")

    async def _analyze(self, camera_id: str, live: CameraLive, latest: asyncio.Queue) -> None:
        analyzer = get_analyzer()
        while True:
            at, jpeg = await latest.get()
            if settings.analysis_provider == "push":
                continue  # детекции присылает внешний сервис (/api/ingest); здесь только следим, что видео идёт
            try:
                result = await analyzer.analyze(jpeg, camera_id=camera_id, taken_at=at)
            except AnalysisError as exc:
                result = AnalysisResult(provider=analyzer.name, supported=False, note=str(exc))
            frame = LiveFrame(at=at, jpeg=jpeg, result=result)
            live.frame = frame
            live.history.append(frame)
            # своя модель: рамки кадра — ещё и в поток рамок (учёт работы техники; браузеру — пока видео не разбирается)
            if result.supported and (tracking := get_relay().local) is not None:
                try:
                    tracking.feed(camera_id, at, result.detections)
                except Exception:  # noqa: BLE001 — сбой рамок не должен останавливать анализ кадров камеры
                    log.exception("Рамки по кадру камеры %s не отправлены", camera_id)

    # ---------- сверка ----------
    async def check(self, site_id: str, *, trigger: str, at: datetime | None = None) -> None:
        now = at or utcnow()
        window = timedelta(seconds=max(settings.check_interval_s, 10))
        async with SessionLocal() as session:
            cameras = list(
                await session.scalars(
                    select(Camera).where(Camera.site_id == site_id, Camera.enabled, Camera.deleted_at.is_(None))
                )
            )
            frames = {}
            for camera in cameras:
                live = self.live.get(camera.id)
                if live is None:
                    continue
                camera.status = "online" if live.online else "offline"
                camera.last_error = None if live.online else live.error
                if live.received_at:
                    camera.last_seen_at = live.received_at
                pick = representative(live.recent(window, now))
                # камера на связи — берём кадр за минуту; связь пропала — годится только совсем свежий
                if pick and (live.online or now - pick.at <= fresh_limit()):
                    frames[camera.id] = pick
            await check_site(session, site_id, at=now, trigger=trigger, frames=frames)

    async def _check_sites(self, site_ids: set[str], trigger: str) -> None:
        for site_id in sorted(site_ids):  # каждый объект отдельно: сбой одного не останавливает остальные
            try:
                await self.check(site_id, trigger=trigger)
            except Exception:
                log.exception("Сбой сверки объекта %s", site_id)

    async def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_round, rounds = loop.time() + min(settings.check_interval_s, 15), 0
        while True:
            if (timeout := next_round - loop.time()) > 0:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
                except TimeoutError:
                    pass
            self._wake.clear()
            if self._pending and loop.time() < next_round:  # внеочередная сверка не сдвигает плановую
                pending, self._pending = self._pending, set()
                await self._check_sites(pending, "rule_change")
                continue
            self._pending.clear()
            next_round = loop.time() + settings.check_interval_s
            try:
                async with SessionLocal() as session:
                    site_ids = set(await session.scalars(select(Site.id)))
            except Exception:
                log.exception("Не удалось получить список объектов")
                continue
            await self._check_sites(site_ids, "schedule")
            if (rounds := rounds + 1) % 10 == 0:
                try:
                    async with SessionLocal() as session:
                        if removed := await cleanup_frames(session):
                            log.info("Удалено старых кадров: %d", removed)
                except Exception:
                    log.exception("Не удалось удалить старые кадры")


def camera_source_url(camera: Camera) -> str:
    """Адрес камеры с паролем — только для шлюза: он сам подключается к камере."""
    from app.services.engine import camera_address

    return camera_address(camera).url(with_credentials=True)


_pipeline: Pipeline | None = None


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline
