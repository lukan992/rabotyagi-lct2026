"""Анализ кадров: демо-анализатор, клиент внешнего сервиса, приём готовых детекций, разбор загруженного фото."""

import io
import json

import httpx
import pytest
from PIL import Image

from app.config import ASSETS_DIR
from app.services.analysis import AnalysisError, get_mock
from app.services.analysis.http import HttpAnalyzer
from tests.conftest import check, login_as

pytestmark = pytest.mark.anyio
SEED = ASSETS_DIR / "seed"


async def test_mock_recognises_recompressed_frames_and_refuses_unknown():
    mock = get_mock()
    with Image.open(SEED / "road-roller-a.jpg") as img:
        out = io.BytesIO()
        img.resize((800, 450)).save(out, "JPEG", quality=55)
    result = await mock.analyze(out.getvalue(), camera_id="t1")
    assert result.supported and [d.type for d in result.detections] == ["roller"]

    noise = io.BytesIO()
    Image.effect_noise((640, 360), 70).convert("RGB").save(noise, "JPEG")
    unknown = await mock.analyze(noise.getvalue())
    assert unknown.supported is False and unknown.detections == [] and "SK_ANALYSIS_PROVIDER" in unknown.note


async def test_http_analyzer_follows_the_contract():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["multipart"] = request.headers["content-type"].startswith("multipart/form-data")
        seen["has_camera"] = b'name="camera_id"' in request.content
        return httpx.Response(
            200,
            json={
                "model": "yolo-test",
                "detections": [
                    {"type": "excavator", "confidence": 0.91, "box": {"x": 10, "y": 20, "w": 30, "h": 40}},
                    {
                        "type": "tower_crane",
                        "confidence": 0.8,
                        "box": {"x": 0, "y": 0, "w": 10, "h": 10},
                    },  # класса нет в справочнике
                    {"type": "truck", "confidence": 0.7, "box": {"x": 90, "y": 95, "w": 30, "h": 30}},  # рамка вылезает за кадр
                ],
            },
        )

    analyzer = HttpAnalyzer("http://ml.local/analyze", "secret-key", 5, transport=httpx.MockTransport(handler))
    result = await analyzer.analyze(b"jpeg-bytes", camera_id="c1")
    assert seen == {"auth": "Bearer secret-key", "multipart": True, "has_camera": True}
    assert result.provider == "http" and result.model == "yolo-test"
    assert [d.type for d in result.detections] == ["excavator", "truck"] and "tower_crane" in result.note
    truck = result.detections[1]
    assert truck.x + truck.w <= 100 and truck.y + truck.h <= 100

    broken = HttpAnalyzer("http://ml.local/analyze", None, 5, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(AnalysisError):
        await broken.analyze(b"x")


async def test_analyze_sample_and_upload(client):
    manager = await login_as(client, "manager")
    sample = (await client.post("/api/analyze", headers=manager, data={"sample": "pit-loading", "siteId": "s1"})).json()
    assert [r["state"] for r in sample["rows"]] == ["ok", "low"]
    assert (
        sample["deviations"][0]["title"] == "Мало самосвалов: 1 из 2"
        and "не меньше 2 самосвалов" in sample["deviations"][0]["why"]
    )

    crane = (await client.post("/api/public/analyze", data={"sample": "gate-crane", "ruleKey": "excavation"})).json()
    assert {d["kind"] for d in crane["deviations"]} == {"missing", "unexpected"}

    upload = await client.post(
        "/api/public/analyze",
        data={"ruleKey": "asphalt"},
        files={"image": ("own.jpg", (SEED / "road-roller-b.jpg").read_bytes(), "image/jpeg")},
    )
    assert upload.json()["supported"] and upload.json()["imageUrl"].startswith("data:image/jpeg")
    junk = await client.post(
        "/api/public/analyze", data={"ruleKey": "asphalt"}, files={"image": ("a.txt", b"hello", "text/plain")}
    )
    assert junk.status_code == 422
    demo = (await client.get("/api/public/demo")).json()
    assert len(demo["rules"]) == 9 and len(demo["samples"]) == 5


async def test_push_mode_ingest(client):
    """Внешний сервис сам разобрал кадр и прислал детекции: два самосвала приехали на котлован."""
    from datetime import timedelta

    from app.db import utcnow

    manager = await login_as(client, "manager")
    detections = json.dumps(
        [
            {"type": "excavator", "confidence": 0.9, "box": {"x": 30, "y": 40, "w": 30, "h": 40}},
            {"type": "dump_truck", "confidence": 0.88, "box": {"x": 5, "y": 50, "w": 20, "h": 20}},
            {"type": "dump_truck", "confidence": 0.86, "box": {"x": 70, "y": 50, "w": 20, "h": 20}},
        ]
    )
    files = {"image": ("frame.jpg", (SEED / "pit-loading.jpg").read_bytes(), "image/jpeg")}
    form = {"camera_id": "c1", "detections": detections, "model": "yolo-test"}
    key = {"X-API-Key": "ingest-test-key"}
    assert (await client.post("/api/ingest/snapshots", data=form, files=files)).status_code == 401
    accepted = await client.post("/api/ingest/snapshots", data=form, files=files, headers=key)
    assert accepted.status_code == 202 and accepted.json()["accepted"] and accepted.json()["detections"] == 3
    # опоздавший кадр картину не меняет, кадр «из будущего» (местное время вместо UTC) — ошибка
    late = {**form, "taken_at": (utcnow() - timedelta(minutes=5)).isoformat()}
    assert (await client.post("/api/ingest/snapshots", data=late, files=files, headers=key)).json()["accepted"] is False
    future = {**form, "taken_at": (utcnow() + timedelta(hours=3)).isoformat()}
    assert (await client.post("/api/ingest/snapshots", data=future, files=files, headers=key)).status_code == 422

    await check("s1")  # ближайшая плановая сверка
    snapshots = (await client.get("/api/snapshots?cameraId=c1", headers=manager)).json()
    assert snapshots[0]["provider"] == "yolo-test"
    shortage = next(
        a
        for a in (await client.get("/api/alerts?siteId=s1", headers=manager)).json()
        if a["equipment"] == "dump_truck" and a["code"] >= "ОТК-26-0138"
    )
    assert shortage["status"] == "resolved" and "снято автоматически" in shortage["history"][-1]["text"]


async def test_ingest_explains_integration_mistakes(client, monkeypatch):
    """Частые ошибки внешнего сервиса — не молчаливые неправильные рамки, а понятный ответ с номером объекта."""
    from app.config import get_settings

    files = {"image": ("frame.jpg", (SEED / "pit-loading.jpg").read_bytes(), "image/jpeg")}
    key = {"X-API-Key": "ingest-test-key"}

    async def send(*objects: dict, headers: dict = key) -> httpx.Response:
        form = {"camera_id": "c2", "detections": json.dumps(list(objects))}
        return await client.post("/api/ingest/snapshots", data=form, files=files, headers=headers)

    box = {"x": 10, "y": 20, "w": 30, "h": 40}
    yolo = await send({"type": "crane", "confidence": 0.9, "box": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}})
    assert yolo.status_code == 422 and "в долях 0–1" in yolo.json()["detail"] and "№ 1" in yolo.json()["detail"]
    pixels = await send(
        {"type": "crane", "confidence": 0.9, "box": box},
        {"type": "crane", "confidence": 0.9, "box": {"x": 320, "y": 180, "w": 640, "h": 360}},
    )
    assert pixels.status_code == 422 and "пиксели" in pixels.json()["detail"] and "№ 2" in pixels.json()["detail"]
    percent = await send({"type": "crane", "confidence": 93, "box": box})
    assert percent.status_code == 422 and "проценты" in percent.json()["detail"]
    # не наша техника — пропускаем и говорим об этом; сервер сам разбирает кадры (mock) — подсказываем включить push
    ok = (await send({"type": "person", "confidence": 0.9, "box": box}, {"type": "crane", "confidence": 0.9, "box": box})).json()
    assert ok["detections"] == 1 and "person" in ok["note"] and "push" in ok["note"]
    # один ключ на сервис: ключ сервиса разметки подходит и для приёма кадров
    monkeypatch.setattr(get_settings(), "tracker_api_key", "tracker-key")
    assert (await send({"type": "crane", "confidence": 0.9, "box": box}, headers={"X-API-Key": "tracker-key"})).status_code == 202
    assert (await send(headers={"X-API-Key": "wrong"})).status_code == 401


async def test_ingest_boxes_follow_padding_to_16_9(client):
    """Камера 4:3: сервис считает рамку по своему кадру, сервер хранит кадр с полями до 16:9 — рамка должна сесть туда же."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1600, 1200), (90, 90, 90)).save(buffer, "JPEG")
    files = {"image": ("frame.jpg", buffer.getvalue(), "image/jpeg")}
    whole = json.dumps([{"type": "crane", "confidence": 0.9, "box": {"x": 0, "y": 0, "w": 100, "h": 100}}])
    sent = await client.post(
        "/api/ingest/snapshots",
        data={"camera_id": "c2", "detections": whole},
        files=files,
        headers={"X-API-Key": "ingest-test-key"},
    )
    assert sent.status_code == 202
    manager = await login_as(client, "manager")
    live = next(c for c in (await client.get("/api/live", headers=manager)).json() if c["cameraId"] == "c2")
    assert live["detections"][0]["box"] == {"x": 12.5, "y": 0.0, "w": 75.0, "h": 100.0}  # по центру, между полями


async def test_ingest_rejects_image_bomb_politely(client):
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    # 66 байт с заголовком «10000×10000 пикселей»: разбор такой картинки — отказ, а не падение сервера (500)
    bomb = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 10000, 10000, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")
    sent = await client.post(
        "/api/ingest/snapshots",
        data={"camera_id": "c1", "detections": "[]"},
        files={"image": ("bomb.png", bomb, "image/png")},
        headers={"X-API-Key": "ingest-test-key"},
    )
    assert sent.status_code == 422
