from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
from time import perf_counter
from typing import Any

from .tracking import Detection

log = logging.getLogger(__name__)


class MappingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ClassMapping:
    version: str
    expected_names: dict[int, str]
    canonical: dict[int, str | None]

    @classmethod
    def load(cls, path: Path) -> "ClassMapping":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            version=raw["version"], expected_names={int(k): v for k, v in raw["model_names"].items()},
            canonical={int(k): entry["canonical"] for k, entry in raw["classes"].items()},
        )

    def verify(self, names: dict[int, str]) -> None:
        observed = {int(key): value for key, value in names.items()}
        if observed != self.expected_names:
            raise MappingError(f"checkpoint class names do not match mapping {self.version}: {observed!r}")

    def equipment_type(self, class_id: int) -> str | None:
        kind = self.canonical.get(class_id)
        if class_id not in self.canonical:
            log.warning("unknown model class excluded", extra={"class_id": class_id})
        return kind


class YOLODetector:
    """One checkpoint in memory; tracker objects are isolated and swapped under one inference lock."""

    def __init__(self, model_path: Path, mapping: ClassMapping, device: str, conf: float, imgsz: int, tracker: str):
        self.model_path, self.mapping, self.device = model_path, mapping, device
        self.conf, self.imgsz, self.tracker = conf, imgsz, tracker
        self.model: Any | None = None
        self._lock = asyncio.Lock()
        self._trackers: dict[str, Any] = {}

    def load(self) -> None:
        if not self.model_path.is_file():
            raise FileNotFoundError(f"YOLO weights not found: {self.model_path}")
        from ultralytics import YOLO

        model = YOLO(str(self.model_path))
        self.mapping.verify(model.names)
        self.model = model
        log.info("model loaded", extra={"model_version": self.mapping.version, "device": self.device or "auto"})

    async def track(self, camera_id: str, frame: Any) -> tuple[list[Detection], float]:
        async with self._lock:
            return await asyncio.to_thread(self._track_sync, camera_id, frame)

    def _track_sync(self, camera_id: str, frame: Any) -> tuple[list[Detection], float]:
        if self.model is None:
            raise RuntimeError("model is not loaded")
        start = perf_counter()
        predictor = getattr(self.model, "predictor", None)
        if predictor is not None:
            if camera_id in self._trackers:
                predictor.trackers = self._trackers[camera_id]
            elif hasattr(predictor, "trackers"):
                # Ultralytics only constructs a tracker when the attribute is absent.
                del predictor.trackers
        results = self.model.track(
            source=frame, persist=True, verbose=False, device=self.device, conf=self.conf, imgsz=self.imgsz,
            tracker=str(Path(__file__).with_name("trackers") / f"{self.tracker}.yaml"),
        )
        predictor = self.model.predictor
        self._trackers[camera_id] = predictor.trackers
        detections: list[Detection] = []
        boxes = results[0].boxes
        if boxes is not None and boxes.id is not None:
            for xyxy, confidence, cls_id, track_id in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), boxes.id.tolist()):
                if not math.isfinite(float(confidence)) or not all(math.isfinite(float(value)) for value in xyxy):
                    log.warning("invalid model detection excluded", extra={"camera_id": camera_id})
                    continue
                kind = self.mapping.equipment_type(int(cls_id))
                if kind is not None:
                    detections.append(Detection(int(track_id), kind, float(confidence), tuple(map(float, xyxy))))
        return detections, (perf_counter() - start) * 1000

    def drop_camera(self, camera_id: str) -> None:
        self._trackers.pop(camera_id, None)
