"""Адреса камер: проверка и быстрая проверка связи по RTSP. Разбор загруженных фото.

С камерой всегда работаем как с видеопотоком RTSP: видео забирает шлюз (services/video.py), кадры на анализ
сервер берёт уже из шлюза. Отдельных «снимков с камеры» нет.

Сервер (и шлюз по его указанию) ходит по адресам, которые вводит пользователь, — это классический риск SSRF. Поэтому:
только rtsp, запрещены служебные адреса (link-local, метаданные облаков, multicast), в адресе нет управляющих символов.
"""

import asyncio
import io
import ipaddress
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import ASSETS_DIR, get_settings

settings = get_settings()

FRAME_SIZE = (1280, 720)  # все кадры приводим к 16:9 — рамки считаются в процентах от такого кадра
# Потолок размера картинки: крошечный PNG 14000×14000 разворачивался в сотни мегабайт памяти. 40 Мп — с запасом для 8K-камер
Image.MAX_IMAGE_PIXELS = 40_000_000
DEFAULT_PORTS = {"rtsp": 554}
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)
# адреса метаданных облаков, которые не попадают под link-local (Alibaba Cloud, AWS по IPv6)
_METADATA_IPS = {ipaddress.ip_address("100.100.100.200"), ipaddress.ip_address("fd00:ec2::254")}


class CameraError(Exception):
    """Ошибка связи с камерой. message — понятная фраза для интерфейса, code — для программной обработки."""

    def __init__(self, message: str, code: str = "unreachable") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class CameraAddress:
    scheme: str  # rtsp
    host: str
    port: int
    path: str = "/"
    username: str | None = None
    password: str | None = None

    def url(self, *, with_credentials: bool = False) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        auth = ""
        if with_credentials and self.username:
            auth = quote(self.username, safe="") + (f":{quote(self.password, safe='')}" if self.password else "") + "@"
        default = DEFAULT_PORTS.get(self.scheme)
        port = "" if self.port == default else f":{self.port}"
        return f"{self.scheme}://{auth}{host}{port}{self.path}"

    @property
    def display(self) -> str:
        """Адрес для интерфейса — без логина и пароля."""
        return self.url()


@dataclass
class ProbeResult:
    ok: bool
    message: str
    code: str = "ok"
    elapsed_ms: int = 0


# ---------- проверка адреса ----------
def validate_address(addr: CameraAddress) -> None:
    if addr.scheme not in DEFAULT_PORTS:
        raise CameraError("Камера подключается видеопотоком RTSP (адрес вида rtsp://…)", "bad_scheme")
    if not 1 <= addr.port <= 65535:
        raise CameraError("Порт должен быть числом от 1 до 65535", "bad_port")
    if not addr.path.startswith("/"):
        raise CameraError("Путь должен начинаться с «/»", "bad_path")
    # перевод строки в пути дописал бы свои строки в запрос RTSP к любому узлу сети
    if any(ch < " " or ch == "\x7f" for part in (addr.host, addr.path, addr.username or "", addr.password or "") for ch in part):
        raise CameraError("В адресе камеры есть недопустимые символы", "bad_path")
    try:
        ipaddress.ip_address(addr.host)
    except ValueError:
        if not _HOST_RE.match(addr.host):
            raise CameraError("Адрес камеры должен быть IP-адресом или именем узла, например 192.168.1.64", "bad_host") from None


def _check_ip(ip: str) -> None:
    address = ipaddress.ip_address(ip)
    if address.is_loopback:
        if not settings.allow_loopback_cameras:
            raise CameraError("Адреса этого компьютера (127.0.0.1) запрещены настройками сервера", "forbidden_address")
        return
    if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved or address in _METADATA_IPS:
        raise CameraError("Этот адрес нельзя использовать для камеры", "forbidden_address")


async def ensure_allowed(addr: CameraAddress) -> None:
    validate_address(addr)
    try:
        _check_ip(addr.host)
        return
    except ValueError:
        pass  # это имя узла — проверяем все адреса, в которые оно раскрывается
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(addr.host, addr.port), timeout=settings.camera_timeout_s
        )
    except (OSError, TimeoutError):
        raise CameraError(f"Не удалось найти узел «{addr.host}». Проверьте адрес.", "dns") from None
    for info in infos:
        _check_ip(info[4][0])


# ---------- связь по RTSP ----------
async def _rtsp_options(addr: CameraAddress) -> int:
    """Быстрая проверка без видео: открываем соединение и отправляем запрос OPTIONS."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(addr.host, addr.port), timeout=settings.camera_timeout_s)
    except TimeoutError:
        raise CameraError(f"Камера не отвечает по адресу {addr.display}: истекло время ожидания", "timeout") from None
    except OSError:
        raise CameraError(f"Не удалось подключиться к {addr.display}. Проверьте адрес, порт и сеть.", "connect") from None
    try:
        writer.write(f"OPTIONS {addr.url()} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: StroyKontrol\r\n\r\n".encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=settings.camera_timeout_s)
    except (TimeoutError, OSError, ValueError):  # ValueError — строка длиннее 64 КБ без перевода строки
        raise CameraError("Порт открыт, но устройство не отвечает по протоколу RTSP", "not_rtsp") from None
    finally:
        writer.close()
    match = re.match(rb"RTSP/\d\.\d (\d{3})", line)
    if not match:
        raise CameraError("Порт открыт, но устройство не отвечает по протоколу RTSP", "not_rtsp")
    return int(match.group(1))


async def probe_rtsp(addr: CameraAddress) -> ProbeResult:
    """Достучаться до камеры по RTSP. Логин и пароль проверит шлюз, когда начнёт забирать видео."""
    started = time.perf_counter()

    def done(**kwargs) -> ProbeResult:
        return ProbeResult(elapsed_ms=int((time.perf_counter() - started) * 1000), **kwargs)

    try:
        await ensure_allowed(addr)
        status = await _rtsp_options(addr)
    except CameraError as exc:
        return done(ok=False, message=exc.message, code=exc.code)
    if status not in (200, 401):
        return done(ok=False, code="rtsp_error", message=f"Камера ответила по RTSP кодом {status}")
    return done(ok=True, message="Камера отвечает по RTSP")


# ---------- фото ----------
def normalize_frame(raw: bytes, *, force_reencode: bool = False) -> bytes:
    """Любую картинку приводим к JPEG 1280×720. Другие пропорции дополняются полями, а не обрезаются."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format == "JPEG" and img.size == FRAME_SIZE and img.mode == "RGB" and not force_reencode:
                return raw  # уже в нужном виде — не пересжимаем
            frame = ImageOps.pad(img.convert("RGB"), FRAME_SIZE, Image.Resampling.LANCZOS, color=(0, 0, 0))
    # SyntaxError Pillow бросает на битом PNG, DecompressionBombError — на слишком большой картинке
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise CameraError("Это не картинка. Загрузите фото JPG или PNG.", "not_image") from None
    out = io.BytesIO()
    frame.save(out, "JPEG", quality=84, optimize=True)
    return out.getvalue()


async def normalize_frame_async(raw: bytes, *, force_reencode: bool = False) -> bytes:
    """То же в отдельном потоке: разбор и пережатие картинки не останавливают сервер для остальных запросов."""
    return await asyncio.to_thread(normalize_frame, raw, force_reencode=force_reencode)


def mock_frame(name: str) -> bytes:
    path = ASSETS_DIR / "seed" / f"{name}.jpg"
    if not path.is_file():
        raise CameraError(f"Демонстрационный кадр «{name}» не найден", "mock_missing")
    return path.read_bytes()
