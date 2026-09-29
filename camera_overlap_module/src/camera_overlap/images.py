"""Bounded image decoding, with explicit displayed-pixel coordinates."""

import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import OverlapConfig
from .errors import InvalidImageError

ImageInput = str | Path | bytes | bytearray | Image.Image
FORMATS = {"JPEG", "PNG", "WEBP", "PPM", "BMP", "TIFF"}


def read_image(value: ImageInput, config: OverlapConfig):
    raw, path = None, None
    try:
        if isinstance(value, (str, Path)):
            path = str(Path(value).resolve())
            with Path(value).open("rb") as stream:
                raw = stream.read(config.max_image_bytes + 1)
        elif isinstance(value, (bytes, bytearray)):
            raw = bytes(value)
        elif not isinstance(value, Image.Image):
            raise InvalidImageError("Expected a path, encoded image bytes, or PIL.Image")
        if raw is not None:
            if len(raw) > config.max_image_bytes:
                raise InvalidImageError(f"Image exceeds {config.max_image_bytes} encoded bytes")
            source = Image.open(BytesIO(raw))
        else:
            source = value
        try:
            if source.format is not None and source.format not in FORMATS:
                raise InvalidImageError(f"Unsupported image format: {source.format}")
            if getattr(source, "n_frames", 1) != 1:
                raise InvalidImageError("Multi-frame images are not supported")
            width, height = source.size
            if width * height > config.max_image_pixels:
                raise InvalidImageError(f"Image exceeds {config.max_image_pixels} pixels")
            if min(width, height) < 32 or round(min(width, height) * config.resize / max(width, height)) < 16:
                raise InvalidImageError("Image is too small or too narrow for feature extraction")
            oriented = ImageOps.exif_transpose(source)
            rgba = oriented.convert("RGBA")
            white = Image.new("RGBA", rgba.size, "white")
            rgb = Image.alpha_composite(white, rgba).convert("RGB")
            meta = {"width": rgb.width, "height": rgb.height,
                    "coordinate_space": "exif_oriented_pixels", "source_path": path,
                    "encoded_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                    "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest()}
            return rgb, meta
        finally:
            if raw is not None:
                source.close()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as error:
        raise InvalidImageError(f"Cannot decode image: {error}") from error
