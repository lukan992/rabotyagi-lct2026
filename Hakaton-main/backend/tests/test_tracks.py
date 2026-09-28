"""Рамки в реальном времени: разбор сообщений сервиса разметки, его доступ к камерам, права браузера на поток рамок."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api import tracks as tracks_api
from app.config import get_settings
from app.main import app
from app.services import video
from app.services.tracks import get_relay, normalize

settings = get_settings()


def test_normalize_keeps_only_valid_objects():
    raw = {
        "camera_id": "c1",
        "ts": "2026-09-25T10:15:03.120+03:00",
        "objects": [
            {"track_id": 17, "type": "excavator", "confidence": 0.93, "box": {"x": 90, "y": 10, "w": 30, "h": 20}},
            {"track_id": 18, "type": "person", "confidence": 0.99, "box": {"x": 1, "y": 1, "w": 5, "h": 5}},  # не техника
            {"track_id": 19, "type": "crane", "confidence": 97, "box": {"x": 1, "y": 1, "w": 5, "h": 5}},  # проценты вместо 0..1
            {"track_id": 20, "type": "roller", "box": {"x": 1}},  # рамка без размеров
        ],
    }
    message, problems = normalize(raw)
    assert message == {
        "cameraId": "c1",
        "ts": "2026-09-25T10:15:03.120+03:00",
        # рамка не выходит за кадр: x сдвинут так, чтобы x + w ≤ 100; номер трека — вместе с типом
        "objects": [
            {
                "trackId": "excavator:17",
                "type": "excavator",
                "confidence": 0.93,
                "box": {"x": 70.0, "y": 10.0, "w": 30.0, "h": 20.0},
            }
        ],
    }
    assert len(problems) == 2 and "проценты" in problems[0] and "box должен быть" in problems[1]
    assert normalize({"camera_id": "c1"})[0] is None and normalize([1, 2])[0] is None


def test_normalize_pads_boxes_of_non_16_9_cameras():
    raw = {
        "camera_id": "c1",
        "frame_w": 1600,
        "frame_h": 1200,
        "objects": [{"track_id": 1, "type": "crane", "confidence": 0.9, "box": {"x": 0, "y": 0, "w": 100, "h": 100}}],
    }
    assert normalize(raw)[0]["objects"][0]["box"] == {"x": 12.5, "y": 0, "w": 75.0, "h": 100}  # как видео 4:3 в плеере 16:9


def test_normalize_explains_common_integration_mistakes():
    box = {"x": 10, "y": 20, "w": 30, "h": 40}
    yolo = {
        "camera_id": "c1",
        "objects": [{"track_id": 1, "type": "crane", "confidence": 0.9, "box": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}}],
    }
    message, problems = normalize(yolo)
    assert message["objects"] == [] and "в долях 0–1" in problems[0]
    no_track = {"camera_id": "c1", "objects": [{"type": "crane", "confidence": 0.9, "box": box}]}
    message, problems = normalize(no_track)
    assert message["objects"][0]["trackId"] == "crane:~0" and "нет track_id" in problems[0]  # рамку всё же показываем
    # трекер нумерует по классам: экскаватор № 1 и самосвал № 1 — две рамки, а не одна
    per_class = {
        "camera_id": "c1",
        "objects": [
            {"track_id": 1, "type": "excavator", "confidence": 0.9, "box": box},
            {"track_id": 1, "type": "dump_truck", "confidence": 0.9, "box": box},
        ],
    }
    assert [o["trackId"] for o in normalize(per_class)[0]["objects"]] == ["excavator:1", "dump_truck:1"]
    twice = {"camera_id": "c1", "objects": [{"track_id": 5, "type": "mixer", "confidence": 0.9, "box": box}] * 2}
    message, problems = normalize(twice)
    assert len(message["objects"]) == 1 and "два объекта с одним track_id" in problems[0]


@pytest.mark.anyio
async def test_tracker_camera_list_needs_its_key(client, monkeypatch):
    assert (await client.get("/api/tracker/cameras")).status_code == 503  # сервис не подключён
    monkeypatch.setattr(settings, "tracker_api_key", "tracker-key")
    assert (await client.get("/api/tracker/cameras", headers={"X-Api-Key": "wrong"})).status_code == 401
    cameras = (await client.get("/api/tracker/cameras", headers={"X-Api-Key": "tracker-key"})).json()
    c1 = next(c for c in cameras if c["id"] == "c1")
    assert c1["rtspUrl"].endswith("/cam-c1") and "@" not in c1["rtspUrl"]  # пароль в адрес не кладём
    assert c1["zoneKind"] == "work" and c1["siteId"] == "s1"
    monkeypatch.setattr(settings, "tracker_rtsp_url", "rtsp://192.168.1.10:8554")  # сервис на другом компьютере
    cameras = (await client.get("/api/tracker/cameras", headers={"X-Api-Key": "tracker-key"})).json()
    assert cameras[0]["rtspUrl"].startswith("rtsp://192.168.1.10:8554/cam-")


@pytest.mark.anyio
async def test_tracker_may_only_read_camera_streams_over_rtsp(client, monkeypatch):
    monkeypatch.setattr(settings, "tracker_api_key", "tracker-key")

    async def ask(**body) -> int:
        body = {"user": video.TRACKER_USER, "password": "tracker-key", "protocol": "rtsp", **body}
        return (await client.post("/api/video/auth", json=body)).status_code

    assert await ask(action="read", path="cam-c4") == 200
    assert await ask(action="publish", path="cam-c4") == 401  # подменить видео камеры нельзя
    assert await ask(action="read", path="probe-abc") == 401
    assert await ask(action="read", path="cam-c4", password="wrong") == 401
    # тот же логин через WebRTC — это «смотреть любую камеру без входа в систему»: нельзя
    assert await ask(action="read", path="cam-c4", protocol="webrtc") == 401


@pytest.mark.anyio
async def test_tracker_status_is_for_admin_and_manager(client):
    from tests.conftest import login_as

    for role, code in (("admin", 200), ("manager", 200), ("inspector", 403), ("foreman", 403)):
        assert (await client.get("/api/tracker/status", headers=await login_as(client, role))).status_code == code
    meta = (await client.get("/api/meta")).json()["tracker"]
    assert "problem" not in meta  # сырые данные сервиса — не для всех


def test_browser_gets_only_its_cameras(monkeypatch):
    # WebSocket умеет только синхронный TestClient: он поднимает приложение в своём потоке со своим циклом событий
    monkeypatch.setattr(
        settings, "tracker_url", "ws://tracker.invalid/stream"
    )  # включён: подключение к несуществующему сервису просто повторяется в фоне
    relay = get_relay()
    frame = {"objects": [{"track_id": 1, "type": "excavator", "confidence": 0.9, "box": {"x": 1, "y": 1, "w": 10, "h": 10}}]}

    with TestClient(app) as http:
        login = http.post("/api/auth/demo-login", json={"role": "foreman"}).json()
        foreman = login["token"]
        for first, code in (({"token": "garbage"}, 4401), ({"subscribe": ["c1"]}, 4401)):  # плохой токен, без токена
            with http.websocket_connect("/api/tracks") as ws:
                ws.send_text(json.dumps(first))
                assert ws.receive()["code"] == code
        with http.websocket_connect("/api/tracks") as ws:  # до входа — не больше 8 КБ, иначе сразу закрываем
            ws.send_text(json.dumps({"token": "x" * 20_000}))
            assert ws.receive()["code"] == 4400
        with http.websocket_connect("/api/tracks") as ws:  # двоичное первое сообщение — тоже
            ws.send_bytes(b"\x00\x01")
            assert ws.receive()["code"] == 4400
        # токен — первым сообщением, а не в адресе: адреса попадают в журналы сервера и nginx
        with http.websocket_connect("/api/tracks") as ws:
            ws.send_text(json.dumps({"token": foreman, "subscribe": ["c1", "c4"]}))  # c4 — камера чужого объекта
            assert json.loads(ws.receive_text()) == {"type": "ready"}
            assert [s.wanted for s in relay.subscribers] == [{"c1"}]
            # публикуем в цикле событий сервера: подписчики не потокобезопасны
            http.portal.call(relay.publish, {"camera_id": "c4", **frame})
            http.portal.call(relay.publish, {"camera_id": "c1", **frame})
            got = json.loads(ws.receive_text())
            assert got["cameraId"] == "c1" and got["objects"][0]["trackId"] == "excavator:1"


def test_stream_closes_when_user_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "tracker_url", "ws://tracker.invalid/stream")
    monkeypatch.setattr(tracks_api, "RECHECK_S", 0.2)  # в жизни — раз в минуту

    with TestClient(app) as http:
        foreman = http.post("/api/auth/demo-login", json={"role": "foreman"}).json()
        admin = http.post("/api/auth/demo-login", json={"role": "admin"}).json()["token"]
        with http.websocket_connect("/api/tracks") as ws:
            ws.send_text(json.dumps({"token": foreman["token"], "subscribe": ["c1"]}))
            assert json.loads(ws.receive_text()) == {"type": "ready"}
            off = http.patch(
                f"/api/users/{foreman['user']['id']}", json={"isActive": False}, headers={"Authorization": f"Bearer {admin}"}
            )
            assert off.status_code == 200
            assert ws.receive()["code"] == 4401  # отключённый сотрудник больше не получает рамки
        http.patch(f"/api/users/{foreman['user']['id']}", json={"isActive": True}, headers={"Authorization": f"Bearer {admin}"})
