"""HTTP adapter for the local camera-overlap model."""

import asyncio
import hmac
import os
from contextlib import asynccontextmanager

from camera_overlap import CameraOverlapError, InvalidImageError, OverlapDetector
from fastapi import FastAPI, File, Header, HTTPException, UploadFile

MAX_IMAGE_BYTES = 50 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.detector = await asyncio.to_thread(OverlapDetector, device=os.getenv("OVERLAP_DEVICE", "cpu"))
    yield


app = FastAPI(title="Camera overlap service", version="0.1.0", lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(
    image0: UploadFile = File(),
    image1: UploadFile = File(),
    x_overlap_token: str | None = Header(default=None),
) -> dict:
    token = os.getenv("OVERLAP_SERVICE_TOKEN")
    if token and (not x_overlap_token or not hmac.compare_digest(x_overlap_token, token)):
        raise HTTPException(401, "Invalid overlap service token")
    data0 = await image0.read(MAX_IMAGE_BYTES + 1)
    data1 = await image1.read(MAX_IMAGE_BYTES + 1)
    if len(data0) > MAX_IMAGE_BYTES or len(data1) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds 50 MiB")
    try:
        result = await asyncio.to_thread(app.state.detector.analyze, data0, data1)
    except InvalidImageError as error:
        raise HTTPException(422, str(error)) from error
    except CameraOverlapError as error:
        raise HTTPException(503, str(error)) from error
    return result.to_dict()
