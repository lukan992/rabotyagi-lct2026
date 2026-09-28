"""Видео: пропуск для шлюза (кому можно смотреть поток) и «что видит анализ прямо сейчас» по каждой камере."""

import asyncio
import re
from collections import Counter
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import SiteIdQuery, scope
from app.models import Camera
from app.schemas import BoxOut, DetectionOut, LiveCameraOut
from app.security import CAMERA_ADDERS, CurrentUser, Session, authenticate, visible_site_ids
from app.services import video
from app.services.pipeline import CameraLive, get_pipeline

router = APIRouter(tags=["Видео"])
_ALLOW, _DENY, _FORBIDDEN = 200, 401, 403
_HLS_CAMERA_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}\Z")
_HLS_PROBE_ID = re.compile(r"[0-9a-f]{12}\Z")


def _hls_stream_path(original_uri: str | None) -> str | None:
    """Принять только однозначный исходный путь камеры или временного предпросмотра nginx."""
    if not original_uri or "%" in original_uri:
        return None
    path, _, query = original_uri.partition("?")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in path):
        return None
    if any(part.partition("=")[0].lower() in {"token", "access_token", "authorization"} for part in query.split("&")):
        return None
    parts = path.split("/")
    if (
        len(parts) < 4
        or parts[0] != ""
        or parts[1] != "hls"
        or any(part in {"", ".", ".."} or "\\" in part for part in parts[3:])
    ):
        return None
    stream_path = parts[2]
    if stream_path.startswith(video.CAMERA_PREFIX):
        camera_id = stream_path.removeprefix(video.CAMERA_PREFIX)
        return stream_path if _HLS_CAMERA_ID.fullmatch(camera_id) else None
    if stream_path.startswith(video.PROBE_PREFIX):
        probe_id = stream_path.removeprefix(video.PROBE_PREFIX)
        return stream_path if _HLS_PROBE_ID.fullmatch(probe_id) else None
    return None


class GatewayAuthIn(BaseModel):
    """Запрос шлюза mediamtx (authMethod: http) перед каждым подключением к потоку."""

    user: str = ""
    password: str = ""
    token: str = ""  # из заголовка Authorization: Bearer … (так браузер передаёт вход при WebRTC)
    ip: str = ""
    action: str = ""  # publish | read | playback …
    path: str = ""
    protocol: str = ""
    id: str | None = None
    query: str = ""


@router.post("/video/auth", include_in_schema=False)
async def gateway_auth(body: GatewayAuthIn, session: Session) -> Response:
    """200 — пустить, 401 — нет. Смотреть поток камеры может тот, кто видит её объект; публиковать — только сервер."""
    if video.is_replay(body.user, body.password, body.path, body.action, body.protocol):
        return Response(status_code=_ALLOW)  # локальный ретранслятор: RTSP read/publish только в replay-*
    if video.is_internal(body.user, body.password):
        return Response(status_code=_ALLOW)  # сам сервер: публикует демо-ролики, берёт кадры на анализ
    if video.is_tracker(body.user, body.password):  # сервис разметки: только читать потоки камер и только по RTSP
        # WebRTC с тем же логином — это «смотреть любую камеру без входа в систему»: сервису он не нужен
        allowed = body.action == "read" and body.protocol in ("rtsp", "rtsps") and body.path.startswith(video.CAMERA_PREFIX)
        return Response(status_code=_ALLOW if allowed else _DENY)
    if body.action not in ("read", "playback"):
        return Response(status_code=_DENY)
    if body.path.startswith(video.DEMO_PREFIX):
        return Response(status_code=_ALLOW)  # демо-ролики не секрет: их забирает сам шлюз для демо-камер
    token = body.token or parse_qs(body.query).get("token", [""])[0]
    if not token:
        return Response(status_code=_DENY)
    try:
        user = await authenticate(session, token)
    except HTTPException:
        return Response(status_code=_DENY)
    if body.path.startswith(video.PROBE_PREFIX):  # предпросмотр в форме «Добавить камеру»
        return Response(status_code=_ALLOW if user.role in CAMERA_ADDERS else _DENY)
    camera_id = video.camera_id_from_path(body.path)
    camera = await session.get(Camera, camera_id) if camera_id else None
    if camera is None or camera.deleted_at is not None:
        return Response(status_code=_DENY)
    allowed = visible_site_ids(user)
    return Response(status_code=_ALLOW if allowed is None or camera.site_id in allowed else _DENY)


@router.get("/video/hls-auth", include_in_schema=False)
async def hls_auth(
    session: Session,
    authorization: str | None = Header(default=None),
    x_hls_auth_request: str | None = Header(default=None),
    x_original_uri: str | None = Header(default=None),
) -> Response:
    """Авторизовать ровно один HLS-ресурс; вызывать может только internal subrequest nginx."""
    if x_hls_auth_request != "1":
        return Response(status_code=_FORBIDDEN)
    scheme, separator, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or token != token.strip():
        return Response(status_code=_DENY)
    stream_path = _hls_stream_path(x_original_uri)
    if stream_path is None:
        return Response(status_code=_FORBIDDEN)
    try:
        user = await authenticate(session, token)
    except HTTPException as error:
        # auth_request only handles 2xx/401/403; fail closed instead of turning a Keycloak or lookup
        # failure into nginx's 500 response.
        return Response(status_code=_DENY if error.status_code == _DENY else _FORBIDDEN)
    if stream_path.startswith(video.PROBE_PREFIX):
        created_at = video.probes.get(stream_path)
        active = created_at is not None and 0 <= asyncio.get_running_loop().time() - created_at < video.PROBE_TTL_S
        return Response(status_code=_ALLOW if user.role in CAMERA_ADDERS and active else _FORBIDDEN)
    camera_id = stream_path.removeprefix(video.CAMERA_PREFIX)
    camera = await session.get(Camera, camera_id)
    if camera is None or camera.deleted_at is not None:
        return Response(status_code=_FORBIDDEN)
    allowed = visible_site_ids(user)
    return Response(status_code=_ALLOW if allowed is None or camera.site_id in allowed else _FORBIDDEN)


def _live_out(camera_id: str, live: CameraLive | None) -> LiveCameraOut:
    frame = live.frame if live else None
    detections = frame.result.detections if frame else []
    return LiveCameraOut(
        camera_id=camera_id,
        online=bool(live and live.online),
        error=live.error if live else None,
        received_at=live.received_at if live else None,
        analyzed_at=frame.at if frame else None,
        analyzed=frame.result.supported if frame else None,
        note=frame.result.note if frame else None,
        detections=[
            DetectionOut(id=f"live{i}", type=d.type, confidence=d.confidence, box=BoxOut(x=d.x, y=d.y, w=d.w, h=d.h))
            for i, d in enumerate(detections)
        ],
        counts=dict(Counter(d.type for d in detections)),
    )


@router.get("/live", response_model=list[LiveCameraOut], summary="Что видит анализ на каждой камере прямо сейчас")
async def live(user: CurrentUser, session: Session, site_id: SiteIdQuery = None) -> list[LiveCameraOut]:
    query = scope(select(Camera).where(Camera.deleted_at.is_(None), Camera.enabled), Camera.site_id, user, site_id)
    pipeline = get_pipeline()
    return [_live_out(c.id, pipeline.state(c.id)) for c in await session.scalars(query.order_by(Camera.position))]
