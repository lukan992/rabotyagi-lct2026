"""Видео с камер через шлюз mediamtx.

Как устроено:
  • Шлюз забирает RTSP-поток каждой камеры и раздаёт его браузерам по WebRTC (задержка около полусекунды).
    Потоки в шлюзе заводит сервер через API шлюза: путь «cam-<id камеры>», источник — адрес камеры с паролем.
    Пароль камеры остаётся на сервере и в шлюзе, браузер его не видит.
  • Демо-ролики сервер сам публикует в шлюз как RTSP-потоки «demo-feed-<ролик>» (ffmpeg крутит файл по кругу).
    Демо-камеры — обычные RTSP-камеры, которые смотрят на такой поток. Отдельного кода для «ненастоящих» камер нет.
  • Кто может смотреть, шлюз спрашивает у сервера (POST /api/video/auth): браузер передаёт тот же токен, что и API,
    и видит только камеры своих объектов. Служебная учётка (публикация роликов, кадры на анализ) — отдельная.
"""

import asyncio
import hashlib
import hmac
import logging
import re
import shutil
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.config import get_settings
from app.services.camera_client import CameraAddress

log = logging.getLogger("stroykontrol.video")
settings = get_settings()

CAMERA_PREFIX = "cam-"
DEMO_PREFIX = "demo-feed-"
PROBE_PREFIX = "probe-"
REPLAY_PREFIX = "replay-"
INTERNAL_USER = "sk-internal"
REPLAY_USER = "sk-replay"
TRACKER_USER = "sk-tracker"  # сервис разметки: читает потоки камер, пароль — его ключ (SK_TRACKER_API_KEY)

# подписи демо-роликов для формы «Добавить камеру» (файлы — assets/clips/<ключ>.mp4)
CLIP_TITLES = {
    "pit-excavator": "Котлован: экскаватор",
    "gate-crane": "Въезд: автокран",
    "foundation-mixer": "Фундамент: бетоносмеситель",
    "gate-mixer": "Въезд: бетоносмеситель",
    "road-roller": "Дорога: каток",
    "road-dumptruck": "Дорога: самосвал",
    "yard-bulldozer": "Площадка: бульдозер",
}


_CREDENTIALS = re.compile(r"(\w+://)[^/@\s]+@")

_REPLAY_PATH = re.compile(rf"{REPLAY_PREFIX}[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def redact(text: str) -> str:
    """ffmpeg печатает адрес потока вместе с логином и паролем — в журнал сервера он попадает без них."""
    return _CREDENTIALS.sub(r"\1***@", text)


def internal_password() -> str:
    """Пароль служебной учётки шлюза выводится из секрета сервера — отдельно его хранить не нужно."""
    return hashlib.sha256(f"video-internal:{settings.secret_key}".encode()).hexdigest()[:32]


def is_internal(user: str, password: str) -> bool:
    return user == INTERNAL_USER and bool(password) and password == internal_password()



def replay_password() -> str:
    """Пароль отдельной учётки для одного локального ретранслятора."""
    return hmac.new(settings.secret_key.encode(), b"video-replay", hashlib.sha256).hexdigest()


def is_replay_path(path: str) -> bool:
    """Имя ретранслятора не может выйти из его отдельного пространства путей."""
    return bool(_REPLAY_PATH.fullmatch(path))


def is_replay(user: str, password: str, path: str, action: str, protocol: str) -> bool:
    """Учётка ретранслятора публикует и читает только свой RTSP-поток."""
    credentials_match = hmac.compare_digest(user, REPLAY_USER) & hmac.compare_digest(password, replay_password())
    return credentials_match and action in ("publish", "read") and protocol == "rtsp" and is_replay_path(path)


def is_tracker(user: str, password: str) -> bool:
    key = settings.tracker_api_key
    return user == TRACKER_USER and bool(key) and hmac.compare_digest(password.encode(), key.encode())


def tracker_rtsp_url(camera_id: str) -> str:
    """Адрес потока камеры для сервиса разметки (без пароля: логин sk-tracker, пароль — ключ сервиса)."""
    base = urlsplit(settings.tracker_rtsp_url or settings.video_rtsp_url)
    return f"rtsp://{base.hostname}:{base.port or 8554}/{camera_path(camera_id)}"


def camera_path(camera_id: str) -> str:
    return f"{CAMERA_PREFIX}{camera_id}"


def camera_id_from_path(path: str) -> str | None:
    return path.removeprefix(CAMERA_PREFIX) if path.startswith(CAMERA_PREFIX) else None


def internal_rtsp_url(path: str) -> str:
    """Адрес потока в шлюзе со служебной учёткой — так сервер берёт кадры и публикует ролики."""
    base = urlsplit(settings.video_rtsp_url)
    return f"rtsp://{INTERNAL_USER}:{internal_password()}@{base.hostname}:{base.port or 8554}/{path}"


def replay_rtsp_url(path: str) -> str:
    """RTSP-адрес ограниченного ретранслятора для ffmpeg и обычной записи камеры."""
    if not is_replay_path(path):
        raise ValueError(f"Некорректный путь ретранслятора: {path!r}")
    base = urlsplit(settings.video_rtsp_url)
    if not base.hostname:
        raise ValueError("Не задан хост SK_VIDEO_RTSP_URL")
    host = f"[{base.hostname}]" if ":" in base.hostname else base.hostname
    return f"rtsp://{REPLAY_USER}:{replay_password()}@{host}:{base.port or 8554}/{path}"


def demo_feed_address(clip: str) -> CameraAddress:
    """Адрес демо-потока, который можно ввести в форму «Добавить камеру» — как у настоящей камеры."""
    base = urlsplit(settings.video_rtsp_url)
    return CameraAddress(scheme="rtsp", host=base.hostname or "127.0.0.1", port=base.port or 8554, path=f"/{DEMO_PREFIX}{clip}")


def demo_clip_of(path: str | None) -> str | None:
    """Путь камеры «/demo-feed-pit» → ролик «pit»: так демо-анализатор знает, что показывает камера."""
    if path and path.startswith(f"/{DEMO_PREFIX}"):
        return path.removeprefix(f"/{DEMO_PREFIX}")
    return None


def available_clips() -> list[str]:
    return sorted(p.stem for p in settings.clips_dir.glob("*.mp4")) if settings.clips_dir.is_dir() else []


class GatewayError(Exception):
    """Шлюз видео недоступен или отказал."""


@dataclass
class PathState:
    name: str
    ready: bool
    source: str | None
    readers: int


class Gateway:
    """Клиент API шлюза mediamtx (v3)."""

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=5.0, transport=self._transport) as client:
                return await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise GatewayError(f"Шлюз видео не отвечает ({self.base_url}): {exc.__class__.__name__}") from exc

    async def set_source(self, name: str, source_url: str) -> None:
        """Завести (или переписать) поток, который шлюз сам забирает с камеры."""
        conf = {"source": source_url, "rtspTransport": "tcp", "sourceOnDemand": False}
        response = await self._request("POST", f"/v3/config/paths/add/{name}", json=conf)
        if response.status_code == 400 and "exist" in response.text:
            response = await self._request("POST", f"/v3/config/paths/replace/{name}", json=conf)
        if response.status_code >= 400:
            raise GatewayError(f"Шлюз не принял поток {name}: {response.status_code} {response.text[:200]}")

    async def remove(self, name: str) -> None:
        response = await self._request("DELETE", f"/v3/config/paths/delete/{name}")
        if response.status_code >= 400 and response.status_code != 404:
            raise GatewayError(f"Шлюз не удалил поток {name}: {response.status_code}")

    async def configured(self) -> dict[str, str]:
        """Заведённые потоки: имя → источник. Нужен, чтобы не переписывать поток без изменений (шлюз переподключился бы)."""
        response = await self._request("GET", "/v3/config/paths/list", params={"itemsPerPage": 1000})
        if response.status_code != 200:
            raise GatewayError(f"Шлюз не отдал список потоков: {response.status_code}")
        return {item["name"]: item.get("source") or "" for item in response.json().get("items", [])}

    async def states(self) -> dict[str, PathState]:
        response = await self._request("GET", "/v3/paths/list", params={"itemsPerPage": 1000})
        if response.status_code != 200:
            return {}
        items = response.json().get("items", [])
        return {
            i["name"]: PathState(
                name=i["name"],
                ready=bool(i.get("ready")),
                source=(i.get("source") or {}).get("type"),
                readers=len(i.get("readers") or []),
            )
            for i in items
        }

    async def state(self, name: str) -> PathState | None:
        response = await self._request("GET", f"/v3/paths/get/{name}")
        if response.status_code != 200:
            return None
        i = response.json()
        return PathState(
            name=name, ready=bool(i.get("ready")), source=(i.get("source") or {}).get("type"), readers=len(i.get("readers") or [])
        )

    async def wait_ready(self, name: str, within_s: float) -> bool:
        """Дождаться, пока поток пойдёт (шлюз подключился к камере и получил видео)."""
        deadline = asyncio.get_running_loop().time() + within_s
        while asyncio.get_running_loop().time() < deadline:
            state = await self.state(name)
            if state and state.ready:
                return True
            await asyncio.sleep(0.5)
        return False


_gateway: Gateway | None = None

# Временные потоки проверки подключения из формы «Добавить камеру»: имя → когда заведён. Живут 10 минут.
PROBE_TTL_S = 600.0
probes: dict[str, float] = {}


def get_gateway() -> Gateway:
    global _gateway
    if _gateway is None:
        _gateway = Gateway(settings.video_api_url)
    return _gateway


# ---------- демо-ролики ----------
class DemoFeeds:
    """Публикует каждый ролик из assets/clips в шлюз по кругу, пока работает сервер. Упал ffmpeg — перезапускаем."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            log.warning("Нет ffmpeg — демо-ролики не публикуются (демо-камеры будут без сигнала)")
            return
        for clip in available_clips():
            if clip not in self._tasks:
                self._tasks[clip] = asyncio.create_task(self._run(ffmpeg, clip), name=f"demo-feed-{clip}")
        log.info("Демо-ролики в шлюзе: %s", ", ".join(self._tasks) or "нет роликов")

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _run(self, ffmpeg: str, clip: str) -> None:
        source = settings.clips_dir / f"{clip}.mp4"
        command = [
            ffmpeg, "-nostdin", "-loglevel", "error", "-re", "-stream_loop", "-1", "-i", str(source),
            "-an", "-c:v", "copy", "-f", "rtsp", "-rtsp_transport", "tcp", internal_rtsp_url(f"{DEMO_PREFIX}{clip}"),
        ]  # fmt: skip
        delay = 2.0
        while True:
            started = asyncio.get_running_loop().time()
            process = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            try:
                _, stderr = await process.communicate()
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                raise
            if asyncio.get_running_loop().time() - started > 60:
                delay = 2.0  # долго работал — это не частый сбой, перезапускаем сразу
            reason = stderr.decode(errors="ignore").strip().splitlines()[-1:] or [f"код {process.returncode}"]
            log.warning("Демо-ролик %s остановился (%s) — перезапуск через %.0f с", clip, redact(reason[0]), delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
