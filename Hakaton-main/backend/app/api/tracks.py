"""Рамки техники в реальном времени: список камер для сервиса разметки и поток рамок для браузера."""

import asyncio
import contextlib
import hmac
import json
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.api.deps import get_site
from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import Camera, EquipmentUsage
from app.schemas import EquipmentUsageOut, TrackerCameraOut, TrackerStatusOut
from app.security import CurrentUser, Session, authenticate, require_roles, visible_site_ids
from app.services import video
from app.services.tracks import Subscriber, get_relay

settings = get_settings()
router = APIRouter(tags=["Рамки в реальном времени"])

# Коды закрытия WebSocket (4000–4999 — свои): браузер по ним понимает, переподключаться или нет
WS_UNAUTHORIZED, WS_DISABLED, WS_UNAVAILABLE, WS_BAD_MESSAGE = 4401, 4404, 4503, 4400
AUTH_TIMEOUT_S = 10.0  # столько ждём первое сообщение с токеном
FIRST_MESSAGE_LIMIT = 8 * 1024  # токен Keycloak и список камер — несколько килобайт
MESSAGE_LIMIT = 64 * 1024
RECHECK_S = 60.0  # раз во сколько секунд перепроверяем вход и права открытого потока


@router.get(
    "/tracker/cameras",
    response_model=list[TrackerCameraOut],
    summary="Камеры для сервиса разметки",
    description="Какие потоки читать из шлюза. Логин шлюза — sk-tracker, пароль — ключ сервиса. Заголовок X-Api-Key — тот же ключ.",
)
async def tracker_cameras(session: Session, x_api_key: Annotated[str | None, Header()] = None) -> list[TrackerCameraOut]:
    key = settings.tracker_api_key
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис разметки не подключён: задайте SK_TRACKER_API_KEY")
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), key.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный ключ")
    cameras = await session.scalars(
        select(Camera).where(Camera.enabled, Camera.deleted_at.is_(None)).order_by(Camera.site_id, Camera.position)
    )
    return [
        TrackerCameraOut(
            id=c.id,
            name=c.name,
            site_id=c.site_id,
            zone_kind=c.zone.kind,
            rtsp_url=video.tracker_rtsp_url(c.id),
            demo_clip=video.demo_clip_of(c.path),
        )
        for c in cameras
    ]


@router.get(
    "/sites/{site_id}/equipment-usage",
    response_model=list[EquipmentUsageOut],
    summary="Сколько работала техника по часам (по рамкам сервиса разметки)",
)
async def equipment_usage(
    site_id: str, user: CurrentUser, session: Session, hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24
) -> list[EquipmentUsageOut]:
    """По часу, камере и типу техники: сколько машин сразу, сколько минут в кадре и сколько из них двигалась.
    Одну машину могут видеть две камеры — складывать минуты разных камер нельзя, лучше брать максимум по зоне."""
    await get_site(session, user, site_id)
    rows = await session.scalars(
        select(EquipmentUsage)
        .where(EquipmentUsage.site_id == site_id, EquipmentUsage.hour >= utcnow() - timedelta(hours=hours))
        .order_by(EquipmentUsage.hour, EquipmentUsage.camera_id, EquipmentUsage.equipment_type)
    )
    return [
        EquipmentUsageOut(
            hour=r.hour,
            camera_id=r.camera_id,
            zone_kind=r.zone_kind,
            type=r.equipment_type,
            max_count=r.max_count,
            present_min=round(r.present_s / 60, 1),
            moving_min=round(r.moving_s / 60, 1),
        )  # fmt: skip
        for r in rows
    ]


@router.get(
    "/tracker/status",
    response_model=TrackerStatusOut,
    dependencies=[require_roles("admin", "manager")],
    summary="Рамки в реальном времени: откуда (своя модель или внешний сервис), идут ли, какие камеры разбираются",
)
async def tracker_status() -> TrackerStatusOut:
    relay = get_relay()
    detector = relay.local.detector if relay.local else None
    return TrackerStatusOut(
        enabled=relay.enabled, source=relay.source, connected=relay.connected, messages=relay.messages,
        last_message_at=relay.last_message_at, problem=relay.problem, problem_at=relay.problem_at,
        viewers=len(relay.subscribers), live_cameras=relay.local.live if relay.local else [],
        model=detector.name if detector else None, model_device=detector.device if detector else None,
        model_ms=round(detector.avg_ms, 1) if detector else None,
    )  # fmt: skip


class _Closed(Exception):
    """Браузер закрыл соединение или прислал то, после чего разговаривать не о чем."""


async def _receive(ws: WebSocket, limit: int) -> dict:
    """Следующее сообщение браузера как словарь. Длиннее limit или двоичное — закрываем: до входа любой мог бы
    прислать 16 МБ и занять разбором JSON цикл событий всего сервера. Непонятный JSON — пустой словарь."""
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise _Closed
    text = message.get("text")
    if text is None or len(text) > limit:
        await ws.close(WS_BAD_MESSAGE, "Сообщение не текст или слишком длинное")
        raise _Closed
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _cameras_allowed(user) -> set[str] | None:  # noqa: ANN001
    """Какие камеры видит пользователь сейчас (None — все). Прорабу объект добавили — новые камеры видны сразу."""
    sites = visible_site_ids(user)
    if sites is None:
        return None
    async with SessionLocal() as session:
        return set(await session.scalars(select(Camera.id).where(Camera.site_id.in_(sites))))


async def _login(token: str):  # noqa: ANN202
    """Пользователь по токену; код закрытия, если нельзя: 4401 — вход не действует, 4503 — сервер входа не отвечает."""
    async with SessionLocal() as session:
        try:
            user = await authenticate(session, token)
        except HTTPException as exc:
            return None, WS_UNAVAILABLE if exc.status_code >= 500 else WS_UNAUTHORIZED
        await session.refresh(user, ["sites"])  # объекты прораба понадобятся уже без этой сессии
        return user, None


@router.websocket("/tracks")
async def tracks(ws: WebSocket) -> None:
    """Браузер: первым сообщением присылает {"token": "…", "subscribe": [id камер на экране]}, получает {"type": "ready"},
    дальше — новые {"subscribe": […]} и рамки только этих камер — и только тех, что пользователю разрешено видеть.

    Токен — в сообщении, а не в адресе (?token=): адреса попадают в журналы сервера и nginx.
    Вход перепроверяется раз в минуту: отключили сотрудника, истёк токен — поток закрывается (4401), браузер переподключится
    со свежим токеном. Права на камеры пересчитываются и при каждой новой подписке.
    """
    await ws.accept()
    relay = get_relay()
    if not relay.enabled:
        await ws.close(WS_DISABLED, "Сервис разметки не подключён")
        return
    try:
        first = await asyncio.wait_for(_receive(ws, FIRST_MESSAGE_LIMIT), AUTH_TIMEOUT_S)
    except (_Closed, TimeoutError):
        with contextlib.suppress(Exception):
            await ws.close(WS_UNAUTHORIZED, "Нужно войти в систему")
        return
    token = first.get("token")
    if not isinstance(token, str) or not token:
        await ws.close(WS_UNAUTHORIZED, "Нужно войти в систему")
        return
    user, refused = await _login(token)
    if refused:
        await ws.close(refused, "Нужно войти в систему" if refused == WS_UNAUTHORIZED else "Сервер входа не отвечает")
        return

    subscriber = Subscriber(allowed=await _cameras_allowed(user))
    if isinstance(first.get("subscribe"), list):
        subscriber.want(first["subscribe"])
    relay.subscribers.add(subscriber)
    await ws.send_text('{"type":"ready"}')

    async def send() -> None:
        while True:
            for text in await subscriber.take():
                await ws.send_text(text)

    async def recheck() -> None:
        nonlocal user
        while True:
            await asyncio.sleep(RECHECK_S)
            fresh, refused = await _login(token)
            if refused == WS_UNAUTHORIZED:  # отключили, удалили, токен истёк
                await ws.close(WS_UNAUTHORIZED, "Вход больше не действует")
                return
            if fresh is not None:  # Keycloak на минуту недоступен — не рвём поток, проверим в следующий раз
                user = fresh
                subscriber.allow(await _cameras_allowed(user))

    sender, checker = asyncio.create_task(send()), asyncio.create_task(recheck())
    try:
        while True:
            message = await _receive(ws, MESSAGE_LIMIT)
            if isinstance(message.get("subscribe"), list):
                subscriber.allow(await _cameras_allowed(user))
                subscriber.want(message["subscribe"])
    except (_Closed, WebSocketDisconnect, RuntimeError):  # RuntimeError — поток уже закрыт проверкой входа
        pass
    finally:
        relay.subscribers.discard(subscriber)
        for task in (sender, checker):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
