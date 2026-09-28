from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import hmac
import logging
from typing import Annotated

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, WebSocket
from pydantic import BaseModel, Field

from .camera_manager import CameraManager
from .config import Settings
from .detector import ClassMapping, YOLODetector
from .outbox import EventOutbox
from .tracking import EquipmentEvent
from .ws_stream import StreamHub


class CameraCommand(BaseModel):
    camera_id: str = Field(min_length=1, max_length=255)
    request_id: str | None = Field(default=None, max_length=255)

log = logging.getLogger("yolo_tracker")


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mapping = ClassMapping.load(settings.mapping_path)
        self.detector = YOLODetector(settings.model_path, self.mapping, settings.device, settings.conf_threshold, settings.imgsz, settings.tracker)
        self.hub = StreamHub(settings.api_key)
        self.client: httpx.AsyncClient | None = None
        self.outbox = EventOutbox(settings.outbox_path, settings.outbox_max_events)
        self.manager: CameraManager | None = None
        self.stop = asyncio.Event()
        self.discovery_task: asyncio.Task[None] | None = None
        self.delivery_task: asyncio.Task[None] | None = None
        self.model_ready = False
        self.discovery_ready = False
        self.model_error: str | None = None

    async def start(self) -> None:
        self.client = httpx.AsyncClient(base_url=self.settings.backend_url, timeout=httpx.Timeout(self.settings.request_timeout_seconds))
        try:
            self.detector.load()  # Explicit one-time startup load: never in a request or worker.
        except Exception as exc:
            self.model_error = str(exc)
            log.exception("model failed to load")
            return
        self.model_ready = True
        self.manager = CameraManager(self.settings, self.detector, self.hub, self.client, self.enqueue)
        self.discovery_task = asyncio.create_task(self._discover(), name="camera-discovery")
        self.delivery_task = asyncio.create_task(self.outbox.drain(self._send_event, self.stop), name="event-delivery")

    async def _discover(self) -> None:
        assert self.manager is not None
        while not self.stop.is_set():
            try:
                await self.manager.reconcile()
                self.discovery_ready = True
            except Exception:
                log.exception("camera discovery failed")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.settings.camera_reconcile_seconds)
            except TimeoutError:
                pass

    async def enqueue(self, event: EquipmentEvent) -> None:
        if not await self.outbox.put(event):
            log.error("event dropped because bounded outbox is full", extra={"event_id": str(event.event_id), "camera_id": event.camera_id})

    async def _send_event(self, payload: dict) -> bool:
        assert self.client is not None
        response = await self.client.post("/api/ingest/equipment-events", headers={"X-Api-Key": self.settings.api_key}, json=payload)
        if response.status_code != 202:
            return False
        try:
            return response.json().get("accepted") is True
        except ValueError:
            return False

    async def close(self) -> None:
        self.stop.set()
        # Discovery can still be awaiting the backend. Cancel it before
        # stopping workers so a late response cannot recreate one.
        if self.discovery_task:
            self.discovery_task.cancel()
            try:
                await self.discovery_task
            except asyncio.CancelledError:
                pass
        if self.manager:
            await self.manager.stop()
        if self.delivery_task:
            self.delivery_task.cancel()
            try:
                await self.delivery_task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.aclose()
        self.outbox.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    service = Service(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title="StroyKontrol real YOLO tracker", lifespan=lifespan)
    app.state.service = service

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "model_loaded": service.model_ready, "model_error": service.model_error}

    @app.get("/ready")
    async def ready() -> dict:
        if not service.model_ready or not service.discovery_ready:
            raise HTTPException(status_code=503, detail="model or camera discovery is not ready")
        return {"ready": True, "model_version": service.mapping.version}

    def authorize(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if not x_api_key or not hmac.compare_digest(x_api_key, settings.api_key):
            raise HTTPException(status_code=401, detail="invalid API key")

    async def control(operation: str, command: CameraCommand, x_api_key: str | None) -> dict:
        authorize(x_api_key)
        if service.manager is None:
            raise HTTPException(status_code=503, detail="camera manager is not ready")
        return await service.manager.command(operation, command.camera_id, command.request_id)

    @app.post("/cameras/start")
    async def start_camera(command: CameraCommand, x_api_key: Annotated[str | None, Header()] = None) -> dict:
        return await control("start", command, x_api_key)

    @app.post("/cameras/stop")
    async def stop_camera(command: CameraCommand, x_api_key: Annotated[str | None, Header()] = None) -> dict:
        return await control("stop", command, x_api_key)

    @app.post("/cameras/restart")
    async def restart_camera(command: CameraCommand, x_api_key: Annotated[str | None, Header()] = None) -> dict:
        return await control("restart", command, x_api_key)

    @app.get("/cameras/status")
    async def camera_status(camera_id: Annotated[str | None, Query()] = None, x_api_key: Annotated[str | None, Header()] = None) -> dict:
        authorize(x_api_key)
        return {"cameras": service.manager.status(camera_id) if service.manager else []}

    @app.get("/status")
    async def status(x_api_key: Annotated[str | None, Header()] = None) -> dict:
        authorize(x_api_key)
        return {"model_version": service.mapping.version, "subscribers": service.hub.subscribers, "cameras": service.manager.status() if service.manager else []}

    @app.websocket("/stream")
    async def stream(ws: WebSocket) -> None:
        await service.hub.serve(ws)

    return app


app = create_app()
