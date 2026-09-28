"""Кадр для сервисов аналитики: байты ровно те, что уйдут, их sha256, тип и размеры (раздел 3 контракта).

Кадры камер у нас — JPEG 1280×720 без EXIF, и уходят как есть. Ориентация EXIF, если она всё же есть (фото, загруженные
вручную), применяется заранее: рамки считаются в пикселях повёрнутой картинки.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import ASSETS_DIR, get_settings

MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_BYTES = 25_000_000
MAX_SIDE = 8192
MAX_PIXELS = 40_000_000
EXIF_ORIENTATION = 0x0112


class ImageProblem(Exception):
    """Кадр нельзя отправить: файла нет, он повреждён или не подходит по лимитам."""


@dataclass(frozen=True)
class FrameImage:
    data: bytes
    media_type: str
    width: int
    height: int
    sha256: str


def photo_path(photo_id: str) -> Path | None:
    """Private persisted upload path; photo IDs are generated internally, never accepted as paths."""
    prefix = "photo_"
    suffix = photo_id.removeprefix(prefix)
    if not suffix or not photo_id.startswith(prefix) or any(char not in "0123456789abcdef" for char in suffix):
        return None
    return get_settings().data_dir / "photos" / photo_id

def media_path(image_url: str) -> Path | None:
    """Файл снимка по его адресу в /media или внутреннему photo:<id> locator."""
    if image_url.startswith("photo:"):
        return photo_path(image_url.removeprefix("photo:"))
    settings = get_settings()
    for prefix, base in (("/media/frames/", settings.frames_dir), ("/media/seed/", ASSETS_DIR / "seed")):
        if image_url.startswith(prefix):
            path = (base / image_url.removeprefix(prefix)).resolve()
            return path if path.is_relative_to(base.resolve()) else None
    return None


def prepare_image(data: bytes) -> FrameImage:
    try:
        with Image.open(BytesIO(data)) as image:
            media_type = MEDIA_TYPES.get(image.format or "")
            if media_type is None:
                raise ImageProblem(f"формат {image.format} не подходит: нужен JPEG, PNG или WebP")
            if getattr(image, "n_frames", 1) != 1:
                raise ImageProblem("в файле несколько кадров, а нужен один")
            if image.getexif().get(EXIF_ORIENTATION, 1) != 1:
                upright = ImageOps.exif_transpose(image).convert("RGB")
                buffer = BytesIO()
                upright.save(buffer, "JPEG", quality=92)
                data, media_type = buffer.getvalue(), "image/jpeg"
        with Image.open(BytesIO(data)) as final:
            final.load()  # повреждённый или недокачанный файл — отказ до отправки, а не у сервиса
            width, height = final.size
    except ImageProblem:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageProblem(f"кадр повреждён или не картинка: {exc}") from None
    if len(data) > MAX_BYTES or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ImageProblem(f"кадр {width}×{height}, {len(data)} байт — больше лимитов сервиса")
    return FrameImage(data, media_type, width, height, hashlib.sha256(data).hexdigest())


def load_image(image_url: str) -> FrameImage:
    path = media_path(image_url)
    if path is None:
        raise ImageProblem(f"снимок {image_url} хранится не у нас")
    try:
        data = path.read_bytes()
    except OSError:
        raise ImageProblem("файла кадра уже нет: старые кадры удаляются") from None
    return prepare_image(data)
