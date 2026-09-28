"""Рамки в реальном времени своей моделью — без внешнего сервиса разметки.

• Камеру кто-то смотрит (браузер подписан на её рамки) — сервер читает её видео из шлюза (ffmpeg, до realtime_fps
  кадров в секунду), разбирает моделью и отдаёт рамки браузеру. Модель одна, а камер может быть несколько — кадры
  разбираются по кругу: каждый раз та камера, которую дольше всех не разбирали, и всегда её самый свежий кадр.
• Камеру никто не смотрит — её видео не разбирается: рамки идут из обычного анализа кадров раз в 2 с (конвейер видео).
  Так учёт работы техники (сколько часов работал экскаватор) ведётся по всем камерам, а процессор не тратится на видео,
  которое никто не видит.
• Номер машины между кадрами (track_id) даёт простой трекер: рамка на новом кадре — продолжение той рамки того же типа,
  с которой она больше всего перекрывается.
"""

import asyncio
import itertools
import logging
import math
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from app.config import get_settings
from app.db import utcnow
from app.services import video
from app.services.analysis.base import DetectedObject
from app.services.detector import Detector

log = logging.getLogger("stroykontrol.realtime")
settings = get_settings()

MATCH_IOU = 0.2  # рамка на новом кадре перекрывается с прошлой хотя бы так — та же машина
NEAR = 1.0  # …или её центр сдвинулся меньше чем на размер машины (быстро едет, а кадры редкие — раз в 2 с)
SHOW_MISSES = 1  # машину не нашли на одном кадре — рамку ещё показываем: детектор иногда «моргает»
FORGET_S = 5.0  # не видно дольше — трек забываем; машина вернётся с новым номером
LINGER_S = 10.0  # камеру перестали смотреть — видео читаем ещё немного: вдруг сразу вернутся на страницу
VIDEO_FRESH_S = 3.0  # кадры видео камеры идут — рамки из анализа раз в 2 с по ней не нужны
WATCH_POLL_S = 0.5
FRAME_W, FRAME_H = 1280, 720  # кадры на анализ — такие; видео для модели готовим так же


Box = tuple[float, float, float, float]  # x, y, w, h — проценты кадра, x и y — левый верхний угол


def iou(a: Box, b: Box) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


def likeness(a: Box, b: Box) -> float:
    """Насколько рамка b похожа на продолжение рамки a; 0 — не похожа.

    Главное — перекрытие. Если оно мало (машина быстро едет, а кадры редкие — раз в 2 с), годится и близость центров
    относительно размера машины; такая оценка всегда ниже порога перекрытия — перекрытие важнее.
    """
    overlap = iou(a, b)
    if overlap >= MATCH_IOU:
        return overlap
    dx = abs(a[0] + a[2] / 2 - b[0] - b[2] / 2) / max(a[2], b[2])
    dy = abs(a[1] + a[3] / 2 - b[1] - b[3] / 2) / max(a[3], b[3])
    distance = math.hypot(dx, dy)
    return MATCH_IOU * (1 - distance / NEAR) if distance < NEAR else 0.0


@dataclass
class _Track:
    id: int
    type: str
    box: Box
    confidence: float
    seen_at: float
    misses: int = 0


class BoxTracker:
    """Номера машин на кадрах одной камеры."""

    def __init__(self) -> None:
        self.tracks: list[_Track] = []
        self._ids = itertools.count(1)

    def update(self, detections: list[DetectedObject], now: float) -> list[dict]:
        """Рамки нового кадра → объекты сообщения браузеру ({trackId, type, confidence, box})."""
        self.tracks = [t for t in self.tracks if now - t.seen_at <= FORGET_S]  # давно не видели — это уже не она
        boxes: list[Box] = [(d.x, d.y, d.w, d.h) for d in detections]
        pairs = []
        for ti, track in enumerate(self.tracks):
            for di, detection in enumerate(detections):
                if track.type == detection.type and (score := likeness(track.box, boxes[di])) > 0:
                    pairs.append((score, ti, di))
        matched_tracks, matched = set(), set()
        for _, ti, di in sorted(pairs, reverse=True):  # жадно, от самых похожих пар: техники в кадре — единицы
            if ti in matched_tracks or di in matched:
                continue
            matched_tracks.add(ti)
            matched.add(di)
            track = self.tracks[ti]
            track.box, track.confidence, track.seen_at, track.misses = boxes[di], detections[di].confidence, now, 0
        for ti, track in enumerate(self.tracks):
            if ti not in matched_tracks:
                track.misses += 1
        for di, detection in enumerate(detections):
            if di not in matched:
                self.tracks.append(_Track(next(self._ids), detection.type, boxes[di], detection.confidence, now))
        return [
            {
                "trackId": f"{t.type}:{t.id}",
                "type": t.type,
                "confidence": t.confidence,
                "box": dict(zip("xywh", t.box, strict=True)),
            }
            for t in self.tracks
            if t.misses <= SHOW_MISSES
        ]


class LiveTracking:
    """Рамки в реальном времени своей моделью. deliver — отдать сообщение (учёт техники и браузеры),
    watched — какие камеры сейчас смотрят."""

    def __init__(self, detector: Detector, deliver: Callable[[dict], None], watched: Callable[[], set[str]]) -> None:
        self.detector = detector
        self._deliver, self._watched = deliver, watched
        self.cameras: set[str] = set()  # включённые камеры — их сообщает конвейер видео
        self.trackers: dict[str, BoxTracker] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._frames: dict[str, tuple[int, datetime, bytes]] = {}  # камера → (номер кадра, когда получен, RGB)
        self._done: dict[str, int] = {}  # камера → номер последнего разобранного кадра
        self._served: dict[str, float] = {}  # камера → когда её кадр последний раз отдавали модели
        self._video_at: dict[str, float] = {}  # камера → когда пришёл последний кадр видео
        self._wanted_at: dict[str, float] = {}  # камера → когда её последний раз смотрели
        self._seq = itertools.count(1)
        self._new_frame = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._ffmpeg = shutil.which("ffmpeg")

    @property
    def realtime(self) -> bool:
        """Разбирается ли видео камер (иначе рамки только из анализа кадров раз в frame_interval_s)."""
        return settings.realtime_fps > 0 and settings.video_enabled and self._ffmpeg is not None

    @property
    def live(self) -> list[str]:
        """Камеры, чьё видео сейчас разбирает модель."""
        return sorted(self._readers)

    def set_cameras(self, camera_ids: Iterable[str]) -> None:
        self.cameras = set(camera_ids)
        for camera_id in set(self.trackers) - self.cameras:  # камеру удалили или выключили
            del self.trackers[camera_id]

    def start(self) -> None:
        if self.realtime and not self._tasks:
            self._tasks = [
                asyncio.create_task(self._watch(), name="realtime-watch"),
                asyncio.create_task(self._infer(), name="realtime-model"),
            ]

    async def stop(self) -> None:
        tasks = [*self._tasks, *self._readers.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks, self._readers = [], {}

    def feed(self, camera_id: str, at: datetime, detections: list[DetectedObject]) -> None:
        """Рамки из обычного анализа кадров (раз в 2 с). Видео камеры сейчас разбирается — рамки и так идут чаще."""
        now = asyncio.get_running_loop().time()
        if now - self._video_at.get(camera_id, -math.inf) < VIDEO_FRESH_S:
            return
        self._publish(camera_id, at, detections, now)

    def _publish(self, camera_id: str, at: datetime, detections: list[DetectedObject], now: float) -> None:
        objects = self.trackers.setdefault(camera_id, BoxTracker()).update(detections, now)
        self._deliver({"cameraId": camera_id, "ts": at.isoformat(), "objects": objects})

    # ---------- какие камеры читать ----------
    async def _watch(self) -> None:
        """Читаем видео камер, которые смотрят сейчас (и ещё LINGER_S после — вдруг сразу вернутся)."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                self._assign(loop.time())
            except Exception:  # noqa: BLE001
                log.exception("Не удалось решить, видео каких камер разбирать")
            await asyncio.sleep(WATCH_POLL_S)

    def _assign(self, now: float) -> None:
        watched = self._watched() & self.cameras
        for camera_id in watched:
            self._wanted_at[camera_id] = now
        for camera_id in list(self._readers):
            if camera_id not in self.cameras or now - self._wanted_at.get(camera_id, -math.inf) > LINGER_S:
                self._stop_reader(camera_id)
        for camera_id in sorted(watched - self._readers.keys()):
            if len(self._readers) >= settings.realtime_max_cameras:
                # места нет — уступает камера, которую уже не смотрят (её держали «на всякий случай»)
                idle = [c for c in self._readers if c not in watched]
                if not idle:
                    break
                self._stop_reader(min(idle, key=lambda c: self._wanted_at.get(c, -math.inf)))
            self._readers[camera_id] = asyncio.create_task(self._read(camera_id), name=f"realtime-{camera_id}")

    def _stop_reader(self, camera_id: str) -> None:
        self._readers.pop(camera_id).cancel()
        for store in (self._frames, self._done, self._served, self._video_at):
            store.pop(camera_id, None)

    # ---------- видео камеры ----------
    def _command(self, camera_id: str) -> list[str]:
        width, height = self.detector.frame_size
        # кадр — как на анализе: 1280×720 с полями до 16:9. Потом вдвое меньше — средним по 2×2 уже в RGB: ровно так
        # модель уменьшает снимки (detector._resize), и уверенность по видео и по снимкам одна и та же
        filters = [
            f"fps={settings.realtime_fps:g}",
            f"scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease",
            f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2",
            "format=rgb24",
        ]
        if (width, height) != (FRAME_W, FRAME_H):
            filters.append(f"scale={width}:{height}:flags=area")
        return [
            self._ffmpeg or "ffmpeg", "-nostdin", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
            "-rtsp_transport", "tcp", "-timeout", str(int(settings.camera_timeout_s * 1_000_000)),
            "-i", video.internal_rtsp_url(video.camera_path(camera_id)), "-an",
            "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]  # fmt: skip

    async def _read(self, camera_id: str) -> None:
        """Видео камеры из шлюза → самый свежий кадр в _frames. Прервалось — переподключаемся с растущей паузой."""
        loop = asyncio.get_running_loop()
        delay = 1.0
        while True:
            started = loop.time()
            try:
                await self._read_video(camera_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — что бы ни случилось, камеру смотрят: пробуем снова
                log.exception("Камера %s: не удалось прочитать видео для рамок", camera_id)
            if loop.time() - started > 30:  # долго работало — это был сбой, а не «камера недоступна»
                delay = 1.0
            log.debug("Камера %s: видео для рамок прервалось, повтор через %.0f с", camera_id, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _read_video(self, camera_id: str) -> None:
        loop = asyncio.get_running_loop()
        width, height = self.detector.frame_size
        size = width * height * 3
        process = await asyncio.create_subprocess_exec(
            *self._command(camera_id), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=size * 2
        )
        try:
            while True:
                data = await process.stdout.readexactly(size)
                self._frames[camera_id] = (next(self._seq), utcnow(), data)
                self._video_at[camera_id] = loop.time()
                self._new_frame.set()
        except asyncio.IncompleteReadError:
            pass  # видео прервалось или шлюз не отдал поток — переподключится _read
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    # ---------- модель ----------
    async def _infer(self) -> None:
        """Модель разбирает кадры по кругу: камера, которую дольше всех не разбирали, и её самый свежий кадр."""
        loop = asyncio.get_running_loop()
        width, height = self.detector.frame_size
        while True:
            await self._new_frame.wait()
            self._new_frame.clear()
            while (camera_id := self._next()) is not None:
                seq, at, data = self._frames[camera_id]
                self._done[camera_id], self._served[camera_id] = seq, loop.time()
                try:
                    detections = await self.detector.run(self.detector.detect_rgb, data, width, height)
                    if camera_id in self._readers:  # пока модель считала, камеру могли перестать смотреть
                        self._publish(camera_id, at, detections, loop.time())
                except Exception:  # noqa: BLE001 — один плохой кадр не должен останавливать рамки всех камер
                    log.exception("Рамки по видео камеры %s: кадр не разобран", camera_id)
                    await asyncio.sleep(1.0)

    def _next(self) -> str | None:
        ready = [c for c, (seq, _, _) in self._frames.items() if self._done.get(c) != seq]
        return min(ready, key=lambda c: self._served.get(c, -math.inf), default=None)
