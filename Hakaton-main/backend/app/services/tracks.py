"""Рамки техники в реальном времени: откуда они берутся и как раздаются браузерам.

Два источника:
  • своя модель (SK_ANALYSIS_PROVIDER=local/auto, см. services/realtime.py) — сама разбирает видео камер, которые смотрят;
  • внешний сервис разметки (SK_TRACKER_URL) — детектор + трекер: сам читает видео камер из шлюза и отдаёт
    по WebSocket сообщения «кадр камеры → объекты с track_id»; сервер держит одно подключение к нему.
Каждому браузеру уходят только те камеры, которые он открыл и которые пользователю разрешено видеть.

Формат сообщения сервиса (одно на обработанный кадр одной камеры):
    {"camera_id": "c1", "ts": "2026-09-25T10:15:03.120+03:00", "frame_w": 1920, "frame_h": 1080,
     "objects": [{"track_id": 17, "type": "excavator", "confidence": 0.93,
                  "box": {"x": 24.5, "y": 41.5, "w": 46.5, "h": 57.0}}]}
box — проценты от кадра, x и y — левый верхний угол; type — один из app.equipment.EQUIPMENT_TYPES.
frame_w/frame_h — размер кадра, по которому посчитаны рамки: у камеры не 16:9 рамки пересчитываются под кадр с полями.
"""

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import websockets

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.services.analysis.base import DetectionError, UnknownType, pad_box, read_detection
from app.services.usage import UsageMeter, cleanup_usage, save

if TYPE_CHECKING:
    from app.services.realtime import LiveTracking

log = logging.getLogger("stroykontrol.tracks")
settings = get_settings()

RETRY_MIN_S, RETRY_MAX_S = 1.0, 30.0
WARN_EVERY_S = 60.0  # одна и та же ошибка в каждом из 15 сообщений в секунду не должна заливать журнал
USAGE_FLUSH_S = 60.0  # раз в столько секунд учёт работы техники дописывается в базу
USAGE_CLEANUP_S = 3600.0  # старый учёт работы техники чистим раз в час — и без конвейера видео


def normalize(raw: Any) -> tuple[dict | None, list[str]]:
    """Сообщение сервиса → (сообщение браузеру, что в нём не так).

    Браузеру — camelCase, только наша техника, рамки в пределах кадра. Непонятное сообщение — None: одно кривое
    сообщение не должно рвать поток остальных. Список проблем — для журнала и /api/meta: по нему разработчик сервиса
    видит, почему рамки не появляются (рамка в долях или пикселях, нет track_id и т. п.).
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("camera_id"), str) or not isinstance(raw.get("objects"), list):
        return None, ['сообщение должно быть {"camera_id": "…", "ts": "…", "objects": [...]}']
    objects, problems, seen = [], [], set()
    # размер кадра, по которому посчитаны рамки: у камеры не 16:9 рамки пересчитываем под кадр с полями, как в плеере
    size = (raw.get("frame_w"), raw.get("frame_h"))
    frame = size if all(isinstance(v, int | float) and v > 0 for v in size) else None
    for index, item in enumerate(raw["objects"]):
        try:
            kind, confidence, x, y, w, h = read_detection(item)
            if frame:
                x, y, w, h = pad_box(x, y, w, h, *frame)
        except UnknownType:
            continue  # люди, легковушки — не наше дело
        except DetectionError as exc:
            problems.append(f"камера {raw['camera_id']}: {exc}")
            continue
        track = item.get("track_id")
        if track is None or track == "":
            problems.append(f"камера {raw['camera_id']}: у объекта нет track_id — рамки будут прыгать, а не ехать")
            # запасной номер (рамку всё же покажем); «~» — такой трек не годится для учёта движения: номер по порядку
            # объектов в сообщении, и при другом порядке он достанется другой машине
            key = f"{kind}:~{index}"
        else:
            # трекеры часто нумеруют треки по классам: экскаватор № 1 и самосвал № 1 — разные машины
            key = f"{kind}:{track}"
        if key in seen:
            problems.append(f"камера {raw['camera_id']}: два объекта с одним track_id {track!r} ({kind}) в одном кадре")
            continue
        seen.add(key)
        objects.append(
            {"trackId": key, "type": kind, "confidence": round(confidence, 3), "box": {"x": x, "y": y, "w": w, "h": h}}
        )
    ts = raw.get("ts") if isinstance(raw.get("ts"), str) else None
    return {"cameraId": raw["camera_id"], "ts": ts, "objects": objects}, problems


@dataclass(eq=False)
class Subscriber:
    """Один браузер: какие камеры ему можно (None — все) и какие он сейчас смотрит.

    По каждой камере держим только последнее сообщение: браузер не успевает — старые рамки заменяются свежими,
    а не копятся в общей очереди, где одна частая камера вытесняла бы остальные.
    """

    allowed: set[str] | None
    requested: set[str] = field(default_factory=set)  # что браузер попросил (права сверяем при каждом пересчёте)
    wanted: set[str] = field(default_factory=set)
    pending: dict[str, str] = field(default_factory=dict)
    ready: asyncio.Event = field(default_factory=asyncio.Event)

    def want(self, camera_ids: list) -> None:
        self.requested = {c for c in camera_ids if isinstance(c, str)}
        self._refilter()

    def allow(self, allowed: set[str] | None) -> None:
        """Права пересчитаны (добавили объект, отключили камеру) — сразу применяем к тому, что смотрит браузер."""
        self.allowed = allowed
        self._refilter()

    def _refilter(self) -> None:
        self.wanted = self.requested if self.allowed is None else self.requested & self.allowed
        for camera_id in list(self.pending):
            if camera_id not in self.wanted:
                del self.pending[camera_id]

    def push(self, camera_id: str, text: str) -> None:
        if camera_id not in self.wanted:
            return
        self.pending[camera_id] = text
        self.ready.set()

    async def take(self) -> list[str]:
        """Дождаться новых рамок и забрать по последнему сообщению с каждой камеры."""
        await self.ready.wait()
        self.ready.clear()
        texts, self.pending = list(self.pending.values()), {}
        return texts


class TrackRelay:
    def __init__(self) -> None:
        self.subscribers: set[Subscriber] = set()
        self.local: LiveTracking | None = None  # своя модель вместо внешнего сервиса (задаётся при запуске сервера)
        self._connected = False  # есть подключение к внешнему сервису
        self.messages = 0  # сколько сообщений пришло с запуска
        self.last_message_at: datetime | None = None
        self.problem: str | None = None  # последняя ошибка формата — видна администратору в /api/tracker/status
        self.problem_at: datetime | None = None
        self._warned_at = 0.0
        self.usage = UsageMeter()
        self._task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None

    @property
    def external(self) -> bool:
        return bool(settings.tracker_url)

    @property
    def source(self) -> str | None:
        """Откуда рамки в реальном времени: service — внешний сервис разметки, model — своя модель по видео, None — ниоткуда."""
        if self.external:
            return "service"
        if self.local is not None and self.local.realtime:
            return "model"
        return None

    @property
    def enabled(self) -> bool:
        """Браузеру есть смысл подключаться к /api/tracks."""
        return self.source is not None

    @property
    def connected(self) -> bool:
        """Рамки могут идти: к внешнему сервису есть подключение / своя модель загружена."""
        return self._connected if self.external else self.local is not None

    def watched(self) -> set[str]:
        """Камеры, которые сейчас кто-то смотрит (с учётом прав на них)."""
        return set().union(*(s.wanted for s in self.subscribers))

    def start(self) -> None:
        if self.external and self._task is None:
            self._task = asyncio.create_task(self._run(), name="track-relay")
        # учёт работы техники — по рамкам любого источника; своя модель без видео тоже даёт рамки (раз в 2 с)
        if (self.external or self.local is not None) and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop(), name="equipment-usage")
        if self.local is not None:
            self.local.start()

    async def stop(self) -> None:
        if self.local is not None:
            await self.local.stop()
            self.local = None
        for task in (self._task, self._flush_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = self._flush_task = None
        try:  # накопленное за последнюю минуту не теряем, но и остановку сервера из-за базы не срываем
            await self.flush()
        except Exception:  # noqa: BLE001
            log.exception("Учёт работы техники при остановке не записан")

    async def flush(self) -> int:
        cells = self.usage.take(asyncio.get_running_loop().time())
        if not cells:
            return 0
        try:
            async with SessionLocal() as session:
                return await save(session, cells)
        except Exception:
            self.usage.restore(cells)  # база не ответила — минута учёта вернётся в счётчик и запишется в следующий раз
            raise

    async def _flush_loop(self) -> None:
        loop = asyncio.get_running_loop()
        cleaned = loop.time()
        while True:
            await asyncio.sleep(USAGE_FLUSH_S)
            try:
                await self.flush()
                if loop.time() - cleaned >= USAGE_CLEANUP_S:  # чистка есть и в конвейере видео, но он бывает выключен
                    cleaned = loop.time()
                    async with SessionLocal() as session:
                        await cleanup_usage(session)
            except Exception:  # noqa: BLE001 — сбой записи не должен останавливать поток рамок
                log.exception("Учёт работы техники не записан")

    def publish(self, raw: Any) -> None:
        """Сообщение внешнего сервиса разметки: проверить формат и раздать."""
        message, problems = normalize(raw)
        if problems:
            now, loop_time = utcnow(), asyncio.get_running_loop().time()
            self.problem, self.problem_at = problems[0], now
            if loop_time - self._warned_at >= WARN_EVERY_S:
                self._warned_at = loop_time
                log.warning("Сервис разметки прислал рамки не по формату: %s", problems[0])
        if message is None:
            self.messages += 1
            self.last_message_at = utcnow()
            return
        self.deliver(message)

    def deliver(self, message: dict) -> None:
        """Рамки в нашем виде (как после normalize) → учёт работы техники и браузеры, которые смотрят эту камеру."""
        now, loop_time = utcnow(), asyncio.get_running_loop().time()
        self.messages += 1
        self.last_message_at = now
        self.usage.add(message, loop_time, now)
        text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        for subscriber in self.subscribers:
            subscriber.push(message["cameraId"], text)

    async def _run(self) -> None:
        headers = {"Authorization": f"Bearer {settings.tracker_api_key}"} if settings.tracker_api_key else {}
        delay = RETRY_MIN_S
        while True:
            try:
                async with websockets.connect(settings.tracker_url, additional_headers=headers, max_size=2**20) as ws:
                    self._connected, delay = True, RETRY_MIN_S
                    log.info("Сервис разметки подключён: %s", settings.tracker_url)
                    async for text in ws:
                        try:
                            self.publish(json.loads(text))
                        except ValueError:
                            continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — сеть, отказ сервиса, неверный адрес: пробуем снова, сервер не падает
                if self._connected or delay == RETRY_MIN_S:
                    log.warning("Сервис разметки недоступен (%s) — переподключение", exc)
            finally:
                self._connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_S)


_relay: TrackRelay | None = None


def get_relay() -> TrackRelay:
    global _relay
    if _relay is None:
        _relay = TrackRelay()
    return _relay
