"""Имитация сервиса разметки (детектор + трекер) — пока настоящего сервиса нет.

Делает то же, что должен делать настоящий сервис, кроме распознавания:
  • раз в 30 с берёт у сервера список камер (GET /api/tracker/cameras, заголовок X-Api-Key);
  • отдаёт по WebSocket /stream рамки с track_id 10 раз в секунду по каждой камере.
Рамки берутся из разметки демо-роликов (app/assets/clips/annotations.json) и плавно «плавают», чтобы было видно,
как интерфейс ведёт технику. Камеры, которые смотрят не на демо-ролик, получают пустой список: имитация их не «видит».

Запуск (рядом с сервером на :8100):
    SK_TRACKER_API_KEY=dev-tracker-key uv run uvicorn app.mock_tracker:app --port 8200
Сервер: SK_TRACKER_URL=ws://127.0.0.1:8200/stream SK_TRACKER_API_KEY=dev-tracker-key
"""

import asyncio
import contextlib
import hmac
import json
import logging
import math
import os
import random
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI, WebSocket, status

from app.config import get_settings

log = logging.getLogger("stroykontrol.mock-tracker")
BACKEND_URL = os.environ.get("MOCK_TRACKER_BACKEND_URL", "http://127.0.0.1:8100").rstrip("/")
API_KEY = os.environ.get("SK_TRACKER_API_KEY", "")
FPS = float(os.environ.get("MOCK_TRACKER_FPS", "10"))
WANDER = float(os.environ.get("MOCK_TRACKER_WANDER", "3"))  # на сколько процентов кадра «плавает» работающая техника
LATENCY = timedelta(milliseconds=80)  # как будто кадр обрабатывался 80 мс: ts — время кадра, а не отправки

cameras: list[dict] = []


def _annotations() -> dict[str, list[dict]]:
    path = get_settings().clips_dir / "annotations.json"
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


ANNOTATIONS = _annotations()


async def _refresh_cameras() -> None:
    """Список камер у сервера: камеры добавляют и удаляют на ходу."""
    global cameras
    async with httpx.AsyncClient(timeout=10) as http:
        while True:
            try:
                response = await http.get(f"{BACKEND_URL}/api/tracker/cameras", headers={"X-Api-Key": API_KEY})
                response.raise_for_status()
                cameras = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Список камер не получен (%s) — повтор", exc)
            # пока списка нет (сервер ещё стартует) — спрашиваем часто, потом раз в 30 с
            await asyncio.sleep(30 if cameras else 3)


def frame(camera: dict, t: float) -> dict:
    """Один «обработанный кадр» камеры: рамки из разметки ролика, смещённые по плавной траектории."""
    objects = []
    for i, item in enumerate(ANNOTATIONS.get(camera.get("demoClip") or "", [])):
        x, y, w, h = item["box"]
        seed = zlib.crc32(f"{camera['id']}:{i}".encode())
        phase = seed % 628 / 100
        if item.get("moving"):
            ax, ay = min(WANDER, x, 100 - x - w), min(WANDER * 0.6, y, 100 - y - h)
            x += ax * math.sin(t * 0.7 + phase)
            y += ay * math.sin(t * 0.45 + phase * 1.3)
        x += random.uniform(-0.15, 0.15)  # как у настоящего детектора: рамка чуть дрожит
        y += random.uniform(-0.15, 0.15)
        objects.append(
            {
                "track_id": seed % 10_000,
                "type": item["type"],
                "confidence": round(min(max(item["confidence"] + random.uniform(-0.02, 0.02), 0), 1), 3),
                "box": {"x": round(max(x, 0), 2), "y": round(max(y, 0), 2), "w": w, "h": h},
            }
        )
    ts = (datetime.now(UTC) - LATENCY).isoformat()
    return {"camera_id": camera["id"], "ts": ts, "frame_w": 1280, "frame_h": 720, "objects": objects}  # ролики — 16:9


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    task = asyncio.create_task(_refresh_cameras())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Имитация сервиса разметки", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "cameras": len(cameras), "fps": FPS, "backend": BACKEND_URL}


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    auth = ws.headers.get("authorization", "")
    if API_KEY and not hmac.compare_digest(auth.encode(), f"Bearer {API_KEY}".encode()):
        await ws.close(status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    try:
        while True:
            t = loop.time()
            for camera in cameras:
                await ws.send_text(json.dumps(frame(camera, t), ensure_ascii=False))
            await asyncio.sleep(1 / FPS)
    except Exception:  # noqa: BLE001 — сервер отключился: просто заканчиваем
        return
