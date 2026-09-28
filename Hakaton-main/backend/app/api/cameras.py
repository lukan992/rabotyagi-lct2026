"""Камеры: список, добавление по IP-адресу (видеопоток RTSP) с живым предпросмотром, правка, включение, удаление.

Камера всегда — видеопоток. Видео забирает шлюз; сервер заводит в нём поток камеры и берёт оттуда кадры на анализ.
"""

import asyncio
import secrets

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.api.deps import SiteIdQuery, get_camera, get_site, scope
from app.config import get_settings
from app.db import utcnow
from app.models import Camera, Site, Zone, new_id
from app.schemas import CameraIn, CameraOut, CameraPatch, ConnectionIn, ProbeOut, camera_out
from app.security import CAMERA_ADDERS, CurrentUser, Session, decrypt_secret, encrypt_secret, require_roles
from app.services import audit, tracker_control, video
from app.services.camera_client import DEFAULT_PORTS, CameraAddress, CameraError, probe_rtsp, validate_address
from app.services.engine import camera_address
from app.services.pipeline import get_pipeline

settings = get_settings()
router = APIRouter(prefix="/cameras", tags=["Камеры"])
admin_only = [require_roles("admin")]
adders = [require_roles(*CAMERA_ADDERS)]  # подключить камеру может и руководитель проекта


def _address(conn: ConnectionIn, *, keep_password: str | None = None) -> CameraAddress:
    host = conn.host.strip().strip("[]")
    path = conn.path.strip() or "/"
    addr = CameraAddress(
        scheme=conn.protocol,
        host=host,
        port=conn.port or DEFAULT_PORTS[conn.protocol],
        path=path if path.startswith("/") else f"/{path}",
        username=(conn.username or "").strip() or None,
        password=conn.password or keep_password,
    )
    try:
        validate_address(addr)
    except CameraError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.message) from None
    return addr


async def _video_preview(addr: CameraAddress) -> ProbeOut | None:
    """Завести в шлюзе временный поток и дождаться видео — форма покажет его вживую. None — шлюз не отвечает."""
    name = f"{video.PROBE_PREFIX}{secrets.token_hex(6)}"
    gateway = video.get_gateway()
    try:
        await gateway.set_source(name, addr.url(with_credentials=True))
        video.probes[name] = asyncio.get_running_loop().time()
        ready = await gateway.wait_ready(name, within_s=settings.camera_timeout_s * 2)
    except video.GatewayError:
        return None
    if not ready:
        await _drop_probe(name)
        return ProbeOut(
            ok=False, code="no_video", elapsed_ms=0,
            message="Камера отвечает, но видео не пришло. Проверьте путь к видеопотоку, логин и пароль.",
        )  # fmt: skip
    return ProbeOut(ok=True, code="ok", elapsed_ms=0, message="Камера отвечает, видео идёт", preview_path=name)


async def _drop_probe(name: str) -> None:
    if name.startswith(video.PROBE_PREFIX):
        video.probes.pop(name, None)
        try:
            await video.get_gateway().remove(name)
        except video.GatewayError:
            pass


@router.get("", response_model=list[CameraOut], summary="Камеры доступных объектов")
async def list_cameras(user: CurrentUser, session: Session, site_id: SiteIdQuery = None) -> list[CameraOut]:
    query = scope(select(Camera).where(Camera.deleted_at.is_(None)), Camera.site_id, user, site_id)
    return [camera_out(c) for c in await session.scalars(query.order_by(Camera.site_id, Camera.position, Camera.created_at))]


@router.post("/probe", response_model=ProbeOut, dependencies=adders, summary="Проверить камеру по адресу, не сохраняя её")
async def probe_camera(conn: ConnectionIn) -> ProbeOut:
    addr = _address(conn)
    result = await probe_rtsp(addr)
    if not result.ok:
        return ProbeOut(ok=False, code=result.code, message=result.message, elapsed_ms=result.elapsed_ms)
    if not settings.video_enabled:
        return ProbeOut(ok=True, code="ok", message=result.message, elapsed_ms=result.elapsed_ms)
    preview = await _video_preview(addr)
    if preview is None:
        return ProbeOut(
            ok=True, code="no_gateway", elapsed_ms=result.elapsed_ms,
            message="Камера отвечает по RTSP. Шлюз видео сейчас не запущен — видео появится, когда он заработает.",
        )  # fmt: skip
    preview.elapsed_ms = result.elapsed_ms
    return preview


@router.delete("/probe/{name}", status_code=status.HTTP_204_NO_CONTENT, dependencies=adders, summary="Закрыть предпросмотр")
async def close_probe(name: str) -> None:
    await _drop_probe(name)


@router.post(
    "",
    response_model=CameraOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=adders,
    summary="Добавить камеру по IP-адресу",
)
async def add_camera(body: CameraIn, user: CurrentUser, session: Session, request: Request) -> CameraOut:
    site = await get_site(session, user, body.site_id)
    addr = _address(body.connection)

    duplicate = await session.scalar(
        select(Camera.id).where(
            Camera.deleted_at.is_(None), Camera.host == addr.host, Camera.port == addr.port, Camera.path == addr.path
        )
    )
    # один демо-ролик могут показывать несколько камер — иначе на показе нельзя было бы добавить ни одной
    if duplicate and video.demo_clip_of(addr.path) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Камера с таким адресом уже добавлена")

    result = await probe_rtsp(addr)
    if not result.ok and not body.allow_offline:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result.message)

    if body.zone_id:
        zone = await session.get(Zone, body.zone_id)
        if zone is None or zone.site_id != body.site_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Зона не относится к этому объекту")
    elif body.new_zone_name and body.new_zone_name.strip():
        zone = Zone(id=new_id("z"), site_id=body.site_id, name=body.new_zone_name.strip(), kind=body.new_zone_kind, position=100)
        session.add(zone)
        await session.flush()
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Выберите зону или введите название новой")

    position = (await session.scalar(select(func.max(Camera.position)).where(Camera.site_id == body.site_id)) or 0) + 1
    camera = Camera(
        id=new_id("cam"),
        site_id=body.site_id,
        zone_id=zone.id,
        name=body.name.strip(),
        source_type="rtsp",
        scheme=addr.scheme,
        host=addr.host,
        port=addr.port,
        path=addr.path,
        username=addr.username,
        password_enc=encrypt_secret(addr.password) if addr.password else None,
        spider_enabled=body.spider_enabled,
        status="unknown" if result.ok else "offline",
        last_error=None if result.ok else result.message,
        position=position,
    )
    session.add(camera)
    audit.record(
        session, request, user, "camera.create", f"Добавил камеру «{camera.name}» ({site.name}), адрес {addr.display}",
        entity_type="camera", entity_id=camera.id, entity_name=camera.name, details={"address": addr.display, "zone": zone.name},
    )  # fmt: skip
    await session.commit()
    await session.refresh(camera)
    try:
        await get_pipeline().sync()  # шлюз начинает забирать видео, конвейер — брать кадры на анализ
    finally:
        await tracker_control.notify_camera("start", camera.id)
    return camera_out(camera)


@router.patch("/{camera_id}", response_model=CameraOut, dependencies=admin_only, summary="Изменить камеру")
async def patch_camera(camera_id: str, body: CameraPatch, user: CurrentUser, session: Session, request: Request) -> CameraOut:
    camera = await get_camera(session, user, camera_id)
    was_enabled = camera.enabled
    before = {
        "name": camera.name,
        "enabled": camera.enabled,
        "spiderEnabled": camera.spider_enabled,
        "zone": camera.zone_id,
        "address": camera_out(camera).address,
    }
    if body.name is not None:
        camera.name = body.name.strip()
    if body.enabled is not None:
        camera.enabled = body.enabled
    if body.spider_enabled is not None:
        camera.spider_enabled = body.spider_enabled
    if body.zone_id is not None:
        zone = await session.get(Zone, body.zone_id)
        if zone is None or zone.site_id != camera.site_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Зона не относится к этому объекту")
        camera.zone_id = zone.id
    if body.connection is not None:
        addr = _address(body.connection, keep_password=decrypt_secret(camera.password_enc))
        camera.scheme, camera.host, camera.port, camera.path, camera.username = (
            addr.scheme,
            addr.host,
            addr.port,
            addr.path,
            addr.username,
        )
        camera.password_enc = encrypt_secret(addr.password) if addr.password else None
        camera.status, camera.last_error = "unknown", None
    after = {
        "name": camera.name,
        "enabled": camera.enabled,
        "spiderEnabled": camera.spider_enabled,
        "zone": camera.zone_id,
        "address": camera_out(camera).address,
    }
    changed = audit.changes(before, after)
    if changed:
        site = await session.get(Site, camera.site_id)
        verb = (
            "Выключил"
            if changed.keys() == {"enabled"} and not camera.enabled
            else "Включил"
            if changed.keys() == {"enabled"}
            else "Изменил"
        )
        audit.record(
            session, request, user, "camera.update", f"{verb} камеру «{camera.name}» ({site.name if site else camera.site_id})",
            entity_type="camera", entity_id=camera.id, entity_name=camera.name, details=changed,
        )  # fmt: skip
    await session.commit()
    await session.refresh(camera)
    operation = (
        "stop"
        if was_enabled and not camera.enabled
        else "start"
        if not was_enabled and camera.enabled
        else "restart"
        if body.connection is not None and camera.enabled
        else None
    )
    if operation == "stop":
        await tracker_control.notify_camera(operation, camera.id)
    try:
        await get_pipeline().sync()
    finally:
        if operation in {"start", "restart"}:
            await tracker_control.notify_camera(operation, camera.id)
    return camera_out(camera)


@router.delete(
    "/{camera_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=admin_only,
    summary="Удалить камеру (кадры-доказательства сохраняются)",
)
async def delete_camera(camera_id: str, user: CurrentUser, session: Session, request: Request) -> None:
    camera = await get_camera(session, user, camera_id)
    camera.deleted_at, camera.enabled = utcnow(), False
    site = await session.get(Site, camera.site_id)
    audit.record(
        session, request, user, "camera.delete", f"Удалил камеру «{camera.name}» ({site.name if site else camera.site_id})",
        entity_type="camera", entity_id=camera.id, entity_name=camera.name, details={"address": camera_out(camera).address},
    )  # fmt: skip
    await session.commit()
    await tracker_control.notify_camera("stop", camera.id)
    await get_pipeline().sync()


@router.post("/{camera_id}/test", response_model=ProbeOut, dependencies=admin_only, summary="Проверить связь с камерой")
async def test_camera(camera_id: str, user: CurrentUser, session: Session) -> ProbeOut:
    camera = await get_camera(session, user, camera_id)
    result = await probe_rtsp(camera_address(camera))
    if not result.ok or not settings.video_enabled:
        return ProbeOut(ok=result.ok, code=result.code, message=result.message, elapsed_ms=result.elapsed_ms)
    try:
        state = await video.get_gateway().state(video.camera_path(camera.id))
    except video.GatewayError:
        return ProbeOut(
            ok=True, code="no_gateway", elapsed_ms=result.elapsed_ms, message="Камера отвечает, но шлюз видео не запущен"
        )
    if state and state.ready:
        watching = f", смотрят: {state.readers}" if state.readers else ""
        return ProbeOut(ok=True, code="ok", elapsed_ms=result.elapsed_ms, message=f"Камера отвечает, видео идёт{watching}")
    return ProbeOut(
        ok=False, code="no_video", elapsed_ms=result.elapsed_ms,
        message="Камера отвечает по RTSP, но видео в шлюз не приходит. Проверьте путь к потоку, логин и пароль.",
    )  # fmt: skip
