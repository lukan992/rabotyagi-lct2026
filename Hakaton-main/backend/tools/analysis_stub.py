"""Заготовка внешнего сервиса анализа кадров.

Реализует контракт, по которому бэкенд обращается к сервису распознавания (см. app/services/analysis/http.py).
Чтобы подключить настоящую модель, замените тело функции detect().

Запуск:
    uv run uvicorn tools.analysis_stub:app --port 8200
Подключение к бэкенду:
    SK_ANALYSIS_PROVIDER=http SK_ANALYSIS_API_URL=http://127.0.0.1:8200/analyze uv run uvicorn app.main:app --port 8100
"""

import io

from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image

app = FastAPI(title="Сервис анализа кадров (заготовка)")

# Классы, которые понимает бэкенд. Названия классов вашей модели приведите к этим.
CLASSES = ["excavator", "dump_truck", "roller", "manipulator", "mixer", "bulldozer", "truck", "crane"]


def detect(image: Image.Image) -> list[dict]:
    """Вернуть найденную технику. Рамка — в процентах от кадра, x и y — левый верхний угол.

    Пример для Ultralytics YOLO (пиксели xyxy → проценты):

        result = model(image)[0]
        width, height = image.size
        detections = []
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append({
                "type": CLASS_MAP[result.names[int(box.cls)]],
                "confidence": float(box.conf),
                "box": {"x": x1 / width * 100, "y": y1 / height * 100,
                        "w": (x2 - x1) / width * 100, "h": (y2 - y1) / height * 100},
            })
        return detections
    """
    return [{"type": "excavator", "confidence": 0.9, "box": {"x": 30.0, "y": 40.0, "w": 30.0, "h": 35.0}}]


@app.post("/analyze")
async def analyze(image: UploadFile = File(...), camera_id: str | None = Form(None), taken_at: str | None = Form(None)) -> dict:
    frame = Image.open(io.BytesIO(await image.read())).convert("RGB")  # бэкенд присылает JPEG 1280×720
    return {"model": "stub-v0", "detections": detect(frame)}
