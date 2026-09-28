"""Камеры: добавление по IP (видеопоток RTSP), пароли, опасные адреса, шлюз видео и «что видит анализ»."""

import asyncio
import contextlib

import httpx
import pytest

from app import replay
from app.services import camera_client as cc
from app.services import video
from app.services.camera_client import CameraAddress, CameraError
from tests.conftest import feed, login_as

pytestmark = pytest.mark.anyio
STREAM = "/Streaming/Channels/101"


@contextlib.asynccontextmanager
async def fake_rtsp(status: bytes = b"200 OK"):
    """Камера, отвечающая на RTSP OPTIONS (видео тут не нужно: его забирает шлюз)."""

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"RTSP/1.0 " + status + b"\r\nCSeq: 1\r\nPublic: OPTIONS, DESCRIBE, SETUP, PLAY\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1]


def _conn(port: int, **extra) -> dict:
    return {"protocol": "rtsp", "host": "127.0.0.1", "port": port, "path": STREAM, **extra}


async def test_probe_and_add_camera_by_ip(client):
    admin = await login_as(client, "admin")
    async with fake_rtsp() as port:
        probe = (await client.post("/api/cameras/probe", headers=admin, json=_conn(port))).json()
        assert probe["ok"] and probe["message"]
        body = {
            "siteId": "s4",
            "name": "Камера 2 — въезд",
            "newZoneName": "Въезд",
            "newZoneKind": "gate",
            "connection": _conn(port),
        }
        created = await client.post("/api/cameras", headers=admin, json=body)
        assert created.status_code == 201, created.text
        camera = created.json()
        assert camera["sourceType"] == "rtsp" and camera["streamPath"] == f"cam-{camera['id']}" and camera["online"]
        assert camera["spiderEnabled"] is False
        assert camera["address"] == f"rtsp://127.0.0.1:{port}{STREAM}" and camera["demo"] is False
        again = await client.post("/api/cameras", headers=admin, json={**body, "name": "Дубль"})
        assert again.status_code == 409

    zones = (await client.get("/api/zones?siteId=s4", headers=admin)).json()
    assert {"Въезд": "gate"}.items() <= {z["name"]: z["kind"] for z in zones}.items()
    log = (await client.get("/api/audit", headers=admin)).json()
    assert log[0]["action"] == "camera.create" and "Камера 2 — въезд" in log[0]["summary"]  # кто добавил — в журнале


async def test_demo_feed_can_back_several_cameras(client):
    """Демо-ролик — не физическая камера: его может показывать ещё одна камера (так на показе добавляют «камеру по IP»)."""
    admin = await login_as(client, "admin")
    feed = (await client.get("/api/meta")).json()["demoFeeds"][0]
    body = {
        "siteId": "s4", "name": "Демо-камера", "zoneId": "z4-yard", "allowOffline": True,
        "connection": {"protocol": "rtsp", "host": feed["host"], "port": feed["port"], "path": feed["path"]},
    }  # fmt: skip
    created = await client.post("/api/cameras", headers=admin, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["demo"] is True


async def test_http_snapshot_cameras_are_gone(client):
    admin = await login_as(client, "admin")
    only_video = await client.post("/api/cameras/probe", headers=admin, json={"protocol": "http", "host": "10.0.0.5"})
    assert only_video.status_code == 422  # с камеры — только видеопоток


async def test_credentials_are_encrypted_and_never_returned(client):
    admin = await login_as(client, "admin")
    async with fake_rtsp(b"401 Unauthorized") as port:  # камера просит пароль — это нормальный ответ на OPTIONS
        created = await client.post(
            "/api/cameras",
            headers=admin,
            json={
                "siteId": "s2",
                "name": "Камера 3",
                "zoneId": "z2-found",
                "connection": _conn(port, username="admin", password="S3cret!"),
            },
        )
    assert created.status_code == 201 and created.json()["hasCredentials"] is True
    assert "S3cret" not in created.text  # пароль наружу не уходит

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Camera
    from app.security import decrypt_secret

    async def stored() -> Camera:
        async with SessionLocal() as session:
            return await session.scalar(select(Camera).where(Camera.id == created.json()["id"]))

    row = await stored()
    assert row.password_enc and "S3cret" not in row.password_enc and decrypt_secret(row.password_enc) == "S3cret!"
    # правка адреса без пароля пароль не стирает
    moved = await client.patch(
        f"/api/cameras/{row.id}", headers=admin, json={"connection": {**_conn(8554), "username": "admin", "password": ""}}
    )
    assert moved.status_code == 200 and decrypt_secret((await stored()).password_enc) == "S3cret!"


async def test_unreachable_camera_is_rejected_unless_allowed(client):
    admin = await login_as(client, "admin")
    async with fake_rtsp() as port:
        pass  # порт закрыт: камеры по этому адресу больше нет
    body = {"siteId": "s4", "name": "Камера 9", "zoneId": "z4-yard", "connection": _conn(port)}
    refused = await client.post("/api/cameras", headers=admin, json=body)
    assert refused.status_code == 422 and "Не удалось подключиться" in refused.json()["detail"]
    saved = await client.post("/api/cameras", headers=admin, json={**body, "allowOffline": True})
    assert saved.status_code == 201 and saved.json()["status"] == "offline" and saved.json()["online"] is False
    camera_id = saved.json()["id"]

    toggled = await client.patch(f"/api/cameras/{camera_id}", headers=admin, json={"enabled": False})
    assert toggled.json()["enabled"] is False
    spider_toggled = await client.patch(f"/api/cameras/{camera_id}", headers=admin, json={"spiderEnabled": True})
    assert spider_toggled.json()["spiderEnabled"] is True
    assert (await client.delete(f"/api/cameras/{camera_id}", headers=admin)).status_code == 204
    assert camera_id not in {c["id"] for c in (await client.get("/api/cameras", headers=admin)).json()}
    actions = [e["action"] for e in (await client.get("/api/audit", headers=admin)).json()]
    assert actions[0] == "camera.delete"
    assert actions.count("camera.update") == 2 and "camera.create" in actions


async def test_tracker_outage_does_not_undo_camera_creation(client, monkeypatch):
    import socket

    from app.services import tracker_control

    unavailable = socket.socket()
    unavailable.bind(("127.0.0.1", 0))
    try:
        monkeypatch.setattr(tracker_control.settings, "tracker_url", f"ws://127.0.0.1:{unavailable.getsockname()[1]}/stream")
        monkeypatch.setattr(tracker_control.settings, "tracker_api_key", "shared-key")
        monkeypatch.setattr(tracker_control.settings, "camera_timeout_s", 0.1)
        admin = await login_as(client, "admin")
        created = await client.post(
            "/api/cameras",
            headers=admin,
            json={"siteId": "s4", "name": "Камера без трекера", "zoneId": "z4-yard", "allowOffline": True, "connection": _conn(8554)},
        )
    finally:
        unavailable.close()

    assert created.status_code == 201, created.text
    assert created.json()["id"] in {camera["id"] for camera in (await client.get("/api/cameras", headers=admin)).json()}


async def test_manager_adds_cameras_but_only_admin_changes_them(client):
    manager = await login_as(client, "manager")
    async with fake_rtsp() as port:
        assert (await client.post("/api/cameras/probe", headers=manager, json=_conn(port))).json()["ok"]
        body = {"siteId": "s3", "name": "Камера 3 — ПК 14", "zoneId": "z3-road", "connection": _conn(port)}
        created = await client.post("/api/cameras", headers=manager, json=body)
    assert created.status_code == 201, created.text
    camera_id = created.json()["id"]
    assert (await client.patch(f"/api/cameras/{camera_id}", headers=manager, json={"enabled": False})).status_code == 403
    assert (await client.delete(f"/api/cameras/{camera_id}", headers=manager)).status_code == 403

    admin = await login_as(client, "admin")
    entry = (await client.get("/api/audit?action=camera", headers=admin)).json()[0]
    assert entry["action"] == "camera.create" and entry["actorRole"] == "manager"  # в журнале видно, кто добавил

    for role in ("foreman", "inspector"):
        auth = await login_as(client, role)
        assert (await client.post("/api/cameras/probe", headers=auth, json={"host": "10.0.0.1"})).status_code == 403
        assert (await client.post("/api/cameras", headers=auth, json=body)).status_code == 403
        assert (await client.delete("/api/cameras/c1", headers=auth)).status_code == 403


@pytest.mark.parametrize(
    ("host", "path", "code"),
    [
        ("169.254.169.254", STREAM, "forbidden_address"),  # метаданные облака
        ("100.100.100.200", STREAM, "forbidden_address"),  # метаданные Alibaba Cloud
        ("0.0.0.0", STREAM, "forbidden_address"),
        ("224.0.0.1", STREAM, "forbidden_address"),
        ("bad host!", STREAM, "bad_host"),
        ("10.0.0.5", "/stream\r\nX-Evil: 1", "bad_path"),  # перевод строки дописал бы свои строки в запрос RTSP
    ],
)
async def test_dangerous_addresses_are_blocked(host, path, code):
    with pytest.raises(CameraError) as error:
        await cc.ensure_allowed(CameraAddress("rtsp", host, 554, path))
    assert error.value.code == code


async def test_loopback_can_be_forbidden(monkeypatch):
    monkeypatch.setattr(cc.settings, "allow_loopback_cameras", False)
    with pytest.raises(CameraError):
        await cc.ensure_allowed(CameraAddress("rtsp", "127.0.0.1", 554, "/"))
    await cc.ensure_allowed(CameraAddress("rtsp", "192.168.1.64", 554, STREAM))  # обычная сеть — можно


async def test_rtsp_probe_against_fake_server():
    async with fake_rtsp() as port:
        assert await cc._rtsp_options(CameraAddress("rtsp", "127.0.0.1", port, STREAM)) == 200
        assert (await cc.probe_rtsp(CameraAddress("rtsp", "127.0.0.1", port, STREAM))).ok
    async with fake_rtsp(b"404 Not Found") as port:
        result = await cc.probe_rtsp(CameraAddress("rtsp", "127.0.0.1", port, STREAM))
    assert not result.ok and "404" in result.message


async def test_gateway_asks_who_may_watch(client):
    """Шлюз спрашивает сервер перед каждым подключением: прораб видит только свои камеры, публикует только сервер."""
    token = {
        role: (await login_as(client, role))["Authorization"].removeprefix("Bearer ") for role in ("foreman", "manager", "admin")
    }

    async def ask(**body) -> int:
        return (await client.post("/api/video/auth", json={"action": "read", **body})).status_code

    assert await ask(path="cam-c1", token=token["foreman"]) == 200  # своя камера
    assert await ask(path="cam-c4", token=token["foreman"]) == 401  # камера чужого объекта
    assert await ask(path="cam-c4", token=token["manager"]) == 200
    assert await ask(path="cam-c4") == 401 and await ask(path="cam-c4", token="garbage") == 401
    assert await ask(path="cam-c4", query=f"token={token['manager']}") == 200  # токен в адресе
    # предпросмотр в форме «Добавить камеру» — тем, кто может добавлять камеры
    assert await ask(path="probe-abc", token=token["admin"]) == 200 and await ask(path="probe-abc", token=token["manager"]) == 200
    assert await ask(path="probe-abc", token=token["foreman"]) == 401
    assert await ask(path="demo-feed-pit-excavator") == 200  # демо-ролики забирает сам шлюз
    assert await ask(action="publish", path="demo-feed-x", token=token["admin"]) == 401  # публиковать — только серверу
    internal = {"user": video.INTERNAL_USER, "password": video.internal_password()}
    assert await ask(action="publish", path="demo-feed-x", **internal) == 200


async def test_hls_rechecks_bearer_for_session_resources(client):
    """URL сессии MediaMTX не становится пропуском без bearer или на чужой объект."""
    foreman = await login_as(client, "foreman")
    token_in_url = foreman["Authorization"].removeprefix("Bearer ")

    async def ask(uri: str, headers: dict[str, str] | None = None) -> int:
        request_headers = {"X-HLS-Auth-Request": "1", "X-Original-URI": uri}
        if headers:
            request_headers.update(headers)
        return (await client.get("/api/video/hls-auth", headers=request_headers)).status_code

    own_master = "/hls/cam-c1/index.m3u8"
    own_segment = "/hls/cam-c1/seg7.m4s?session=7ae55e9e-8c5e-4da5-a5e9-a4269122f6c6"
    assert await ask(own_master, foreman) == 200
    assert await ask(own_segment, foreman) == 200
    assert await ask(f"{own_segment}&token={token_in_url}") == 401
    assert await ask("/hls/cam-c4/seg7.m4s?session=7ae55e9e-8c5e-4da5-a5e9-a4269122f6c6", foreman) == 403
    assert await ask("/hls/cam-c1%2Findex.m3u8", foreman) == 403
    assert (
        await client.get(
            "/api/video/hls-auth",
            headers={"X-Original-URI": own_master, **foreman},
        )
    ).status_code == 403


async def test_hls_probe_preview_requires_live_probe_and_camera_adder(client, monkeypatch):
    """Предпросмотр доступен создателю камер только пока его случайный поток существует."""
    admin = await login_as(client, "admin")
    foreman = await login_as(client, "foreman")
    name = "probe-0123456789ab"
    monkeypatch.setitem(video.probes, name, asyncio.get_running_loop().time())

    async def ask(uri: str, credentials: dict[str, str] | None = None) -> int:
        headers = {"X-HLS-Auth-Request": "1", "X-Original-URI": uri, **(credentials or {})}
        return (await client.get("/api/video/hls-auth", headers=headers)).status_code

    resource = f"/hls/{name}/video1_stream.m3u8?session=preview-session"
    assert await ask(resource, admin) == 200
    assert await ask(resource, foreman) == 403
    assert await ask(resource) == 401
    assert await ask("/hls/probe-ffffffffffff/index.m3u8", admin) == 403
    assert await ask(f"/hls/{name}%2Findex.m3u8", admin) == 403
    monkeypatch.setitem(video.probes, name, asyncio.get_running_loop().time() - video.PROBE_TTL_S - 1)
    assert await ask(resource, admin) == 403


async def test_replay_account_is_limited_to_its_rtsp_namespace(client, monkeypatch):
    """Локальный ретранслятор не становится служебным пропуском ко всему шлюзу."""
    replay = {"user": video.REPLAY_USER, "password": video.replay_password()}

    async def ask(**body) -> int:
        return (await client.post("/api/video/auth", json={**replay, **body})).status_code

    assert await ask(action="publish", protocol="rtsp", path="replay-local") == 200
    assert await ask(action="read", protocol="rtsp", path="replay-local") == 200
    assert await ask(action="playback", protocol="rtsp", path="replay-local") == 401
    assert await ask(action="read", protocol="webrtc", path="replay-local") == 401
    assert await ask(action="read", protocol="rtsps", path="replay-local") == 401
    assert await ask(action="read", protocol="rtsp", path="replay-") == 401
    assert await ask(action="read", protocol="rtsp", path="cam-c1") == 401
    assert (
        await client.post(
            "/api/video/auth",
            json={"user": video.REPLAY_USER, "password": "wrong", "action": "read", "protocol": "rtsp", "path": "replay-local"},
        )
    ).status_code == 401

    monkeypatch.setattr(video.settings, "video_rtsp_url", "rtsp://video:8554")
    assert video.replay_rtsp_url("replay-local").endswith("@video:8554/replay-local")


def test_replay_only_copies_webrtc_compatible_h264():
    compatible = {"codec_name": "h264", "profile": "Constrained Baseline", "pix_fmt": "yuv420p", "has_b_frames": 0}
    assert replay._can_copy(compatible)
    assert not replay._can_copy({**compatible, "profile": "High"})
    assert not replay._can_copy({**compatible, "pix_fmt": "yuvj420p"})
    assert not replay._can_copy({**compatible, "has_b_frames": 1})
    assert not replay._can_copy({**compatible, "codec_name": "hevc"})


async def test_pipeline_keeps_gateway_in_step_with_cameras(client, monkeypatch):
    """Каждой включённой камере — поток в шлюзе; неизменный поток не переписывается (шлюз переподключился бы)."""
    from app.services import pipeline

    configured = {"cam-c1": "rtsp://127.0.0.1:8554/demo-feed-pit-excavator", "cam-gone": "rtsp://10.0.0.9/"}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v3/config/paths/list":
            return httpx.Response(200, json={"items": [{"name": k, "source": v} for k, v in configured.items()]})
        return httpx.Response(200, json={})

    monkeypatch.setattr(video, "_gateway", video.Gateway("http://gw", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(pipeline.settings, "video_enabled", True)
    monkeypatch.setattr(pipeline.Pipeline, "_sample", lambda self, camera_id: asyncio.sleep(0))  # кадры не читаем
    await pipeline.get_pipeline().sync()

    added = {path.rsplit("/", 1)[1] for method, path in calls if path.startswith("/v3/config/paths/add/")}
    assert "cam-c1" not in added and {"cam-c2", "cam-c8"} <= added  # c1 уже заведён тем же адресом
    assert ("DELETE", "/v3/config/paths/delete/cam-gone") in calls  # поток удалённой камеры убран


async def test_live_shows_what_analysis_sees_now(client):
    await feed("c1", "pit-excavator")
    foreman = await login_as(client, "foreman")
    live = {c["cameraId"]: c for c in (await client.get("/api/live", headers=foreman)).json()}
    assert set(live) == {"c1", "c2", "c3"}  # только камеры своего объекта
    assert live["c1"]["online"] and live["c1"]["counts"] == {"excavator": 1} and live["c1"]["detections"][0]["box"]["w"] > 0
    assert live["c2"]["online"] is False and live["c2"]["detections"] == []
