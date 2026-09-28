"""Best-effort lifecycle commands for an external YOLO tracker."""

import logging
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx

from app.config import get_settings

log = logging.getLogger("stroykontrol.tracker-control")
settings = get_settings()

CameraOperation = Literal["start", "stop", "restart"]


def control_base_url() -> str | None:
    """Derive the tracker's HTTP control origin from its configured WebSocket URL."""
    tracker_url = settings.tracker_url
    if not tracker_url:
        return None
    try:
        parsed = urlsplit(tracker_url)
        valid = parsed.scheme in {"ws", "wss", "http", "https"} and parsed.hostname is not None
    except ValueError:
        valid = False
    if not valid:
        log.warning("Некорректный SK_TRACKER_URL: не отправляем команду жизненного цикла камеры")
        return None
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    return urlunsplit((scheme, parsed.netloc, "", "", ""))


async def notify_camera(operation: CameraOperation, camera_id: str) -> None:
    """Request a post-commit worker change; reconciliation repairs an unavailable tracker later."""
    base_url = control_base_url()
    if base_url is None or not settings.tracker_api_key:
        return
    try:
        async with httpx.AsyncClient(timeout=settings.camera_timeout_s) as client:
            response = await client.post(
                f"{base_url}/cameras/{operation}",
                headers={"X-Api-Key": settings.tracker_api_key},
                json={"camera_id": camera_id, "request_id": str(uuid4())},
            )
    except httpx.HTTPError as exc:
        log.warning("Не отправлена команда %s для камеры %s: %s; сервис сверит камеры позже", operation, camera_id, exc)
        return

    if response.status_code in {404, 405}:
        log.info("Сервис разметки не поддерживает команды камер; он сверит их позже")
    elif response.is_error:
        log.warning(
            "Сервис разметки отклонил команду %s для камеры %s: HTTP %s; сервис сверит камеры позже",
            operation,
            camera_id,
            response.status_code,
        )
    else:
        try:
            body = response.json()
            accepted = isinstance(body, dict) and body.get("accepted") is True
        except ValueError:
            accepted = False
        if not accepted:
            log.warning(
                "Сервис разметки не принял команду %s для камеры %s; сервис сверит камеры позже",
                operation,
                camera_id,
            )
