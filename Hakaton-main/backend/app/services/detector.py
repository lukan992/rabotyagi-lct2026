"""Своя модель распознавания техники: YOLO в формате ONNX, работает прямо в сервере (ONNX Runtime, процессор).

Сейчас это YOLO26m, дообученная на датасете MOCS (Moving Objects in Construction Sites, 13 классов). Из них техника ТЗ:
экскаватор, самосвал (в MOCS он — Truck), каток, бульдозер, автокран (Crane), бетоносмеситель (Concrete mixer).
Люди, башенные краны, крюки, погрузчики, бетононасосы, сваебои и легковушки в список техники ТЗ не входят — их рамки
не показываем. Кран-манипулятора и грузовика в MOCS нет: их эта модель не найдёт.

Новая версия модели (файл .pt от Ultralytics) — см. tools/export_model.py: переводит в ONNX и кладёт в
models/detector.onnx. Классы и название модель несёт в себе, код менять не нужно.

Кадры разбираются строго по одному в отдельном потоке: ONNX Runtime отпускает GIL — сервер в это время отвечает
на запросы, — а сам запуск и так занимает все ядра, два параллельных только мешали бы друг другу.
"""

import ast
import asyncio
import io
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.config import get_settings
from app.equipment import EQUIPMENT
from app.services.analysis.base import DetectedObject, clamp_box

log = logging.getLogger("stroykontrol.detector")

PAD = 114  # серые поля вокруг кадра — как при обучении (letterbox Ultralytics)
# Отбор рамок (NMS): из перекрытых сильнее остаётся самая уверенная. Порог — как у Ultralytics по умолчанию, но общий для
# всех классов: одну машину модель может назвать двумя классами (самосвал и бетононасос) — рамка должна быть одна
NMS_IOU = 0.7
DEVICES = {"CPUExecutionProvider": "CPU", "CoreMLExecutionProvider": "CoreML", "CUDAExecutionProvider": "CUDA"}


def _key(name: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "_", name.lower()).strip("_")


# Имена классов модели → техника ТЗ (сравниваются без регистра; всё, кроме букв и цифр, — «_»).
# Наши собственные имена (excavator, dump_truck…) подходят как есть.
ALIASES = {
    "concrete_mixer": "mixer",
    "concrete_mixer_truck": "mixer",
    "mixer_truck": "mixer",
    "mobile_crane": "crane",
    "truck_crane": "crane",
    "crane_truck": "crane",
    "dumptruck": "dump_truck",
    "tipper": "dump_truck",
    "tipper_truck": "dump_truck",
    "road_roller": "roller",
    "dozer": "bulldozer",
    "crane_manipulator": "manipulator",
    "knuckle_boom_crane": "manipulator",
    "loader_crane": "manipulator",
    **{_key(e.name): kind for kind, e in EQUIPMENT.items()},  # «Экскаватор», «Кран-манипулятор»
}


def class_map(names: dict[int, str], overrides: dict[str, str] | None = None) -> dict[int, str]:
    """Номер класса модели → тип техники ТЗ. Классов не из ТЗ (люди, башенные краны) в ответе нет.

    overrides — поправки из настроек (SK_DETECTOR_CLASSES): {"имя класса модели": "тип"}, "" — не показывать.
    """
    fixed = {_key(k): v for k, v in (overrides or {}).items()}
    mapping = {}
    for index, name in names.items():
        key = _key(name)
        kind = fixed[key] if key in fixed else key if key in EQUIPMENT else ALIASES.get(key)
        if kind:
            mapping[index] = kind
    # В MOCS нет отдельного самосвала: Truck там — самосвалы на земляных работах. Модель, которая различает
    # самосвал и грузовик, отдаёт оба класса — тогда берём как есть.
    if "dump_truck" not in mapping.values():
        for index, kind in mapping.items():
            if kind == "truck" and _key(names[index]) not in fixed:
                mapping[index] = "dump_truck"
    return mapping


def _names(meta: dict[str, str]) -> dict[int, str]:
    """Классы из метаданных модели: Ultralytics пишет их строкой вида "{0: 'Worker', 1: 'Static crane'}"."""
    try:
        names = ast.literal_eval(meta.get("names", ""))
    except (ValueError, SyntaxError):
        return {}
    if isinstance(names, dict):
        return {int(k): str(v) for k, v in names.items()}
    if isinstance(names, list):
        return dict(enumerate(map(str, names)))
    return {}


def _iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    """Перекрытие рамки [x1, y1, x2, y2] с каждой из others."""
    w = np.clip(np.minimum(box[2], others[:, 2]) - np.maximum(box[0], others[:, 0]), 0, None)
    h = np.clip(np.minimum(box[3], others[:, 3]) - np.maximum(box[1], others[:, 1]), 0, None)
    inter = w * h
    union = (box[2] - box[0]) * (box[3] - box[1]) + (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1]) - inter
    return inter / np.maximum(union, 1e-9)


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Из сильно перекрытых рамок (IoU ≥ threshold) остаётся самая уверенная."""
    order = np.argsort(-scores, kind="stable")
    keep = []
    while order.size:
        best, order = order[0], order[1:]
        keep.append(int(best))
        order = order[_iou(boxes[best], boxes[order]) < threshold]
    return keep


def _resize(image: Image.Image, width: int, height: int) -> np.ndarray:
    """Уменьшить кадр так же, как Ultralytics (OpenCV) при обучении и проверке модели: от этого зависит уверенность.

    Кадр ровно вдвое больше (наши 1280×720 → 640×360) — среднее по квадратам 2×2, ровно как cv2.resize INTER_LINEAR
    в этом случае. Иначе — билинейно средствами Pillow: разница с OpenCV тогда небольшая, но есть.
    """
    pixels = np.asarray(image)
    if image.size == (width, height):
        return pixels
    if image.size == (width * 2, height * 2):
        blocks = pixels.reshape(height, 2, width, 2, 3).astype(np.uint16).sum(axis=(1, 3))
        return ((blocks + 2) // 4).astype(np.uint8)
    return np.asarray(image.resize((width, height), Image.Resampling.BILINEAR))


class Detector:
    """Модель в памяти: кадр → рамки техники ТЗ в процентах кадра."""

    def __init__(self, session: Any, *, name: str, confidence: float, overrides: dict[str, str] | None = None) -> None:
        self.session = session
        meta = session.get_modelmeta().custom_metadata_map
        model_input = session.get_inputs()[0]
        self.input_name = model_input.name
        height, width = model_input.shape[2:4]
        if not isinstance(height, int) or not isinstance(width, int):  # размер входа не задан в модели
            height = width = 640
        self.input_h, self.input_w = height, width
        self.names = _names(meta)
        self.classes = class_map(self.names, overrides)
        self.confidence = confidence
        self.name = meta.get("title") or name
        self.map50 = meta.get("map50")  # точность на проверочной выборке при обучении, если записана при выгрузке
        # формат выхода пишет Ultralytics при выгрузке; нет записи — узнаем по форме ответа
        self.end2end = {"True": True, "False": False}.get(meta.get("end2end", ""))
        self.device = DEVICES.get(session.get_providers()[0], session.get_providers()[0])
        self.runs, self.avg_ms = 0, 0.0
        self._executor = ThreadPoolExecutor(1, thread_name_prefix="detector")

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        confidence: float,
        threads: int = 0,
        accelerate: bool = True,
        overrides: dict[str, str] | None = None,
    ) -> "Detector":
        import onnxruntime as ort  # тяжёлый импорт — только когда модель действительно нужна

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads  # 0 — решает ONNX Runtime (по числу ядер)
        options.inter_op_num_threads = 1
        options.log_severity_level = 3  # только ошибки: предупреждения ORT при загрузке журналу не нужны
        available = ort.get_available_providers()
        providers: list = []
        if accelerate and "CUDAExecutionProvider" in available:
            providers.append("CUDAExecutionProvider")
        if accelerate and "CoreMLExecutionProvider" in available:
            # MLProgram считает во float32: ответы те же, что на процессоре, а на M4 кадр вдвое быстрее (39 мс против 72)
            providers.append(("CoreMLExecutionProvider", {"ModelFormat": "MLProgram"}))
        try:
            session = ort.InferenceSession(str(path), options, providers=[*providers, "CPUExecutionProvider"])
        except Exception:  # noqa: BLE001 — ускоритель не поднялся (драйвер, версия системы): процессор справится сам
            if not providers:
                raise
            log.exception("Ускоритель для модели не запустился — считаем на процессоре")
            session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        detector = cls(session, name=path.stem, confidence=confidence, overrides=overrides)
        # прогрев: первые запуски (у CoreML — сборка модели) долгие, пусть они будут до первого кадра с камеры;
        # последний — оценка обычного времени на кадр для журнала
        blank = Image.new("RGB", detector.frame_size)
        for _ in range(3):
            detector.detect(blank)
        detector.runs = 0
        return detector

    @property
    def frame_size(self) -> tuple[int, int]:
        """Кадр 16:9 шириной с вход модели: такой модель берёт без масштабирования (видео в реальном времени)."""
        return self.input_w, round(self.input_w * 9 / 16)

    @property
    def ignored(self) -> list[str]:
        """Классы модели, которые не техника ТЗ."""
        return [name for index, name in sorted(self.names.items()) if index not in self.classes]

    # ---------- разбор кадра (в потоке модели) ----------
    def detect(self, image: Image.Image) -> list[DetectedObject]:
        started = time.perf_counter()
        tensor, scale, left, top = self._prepare(image)
        output = self.session.run(None, {self.input_name: tensor})[0]
        found = self._parse(output, image.width, image.height, scale, left, top)
        ms = (time.perf_counter() - started) * 1000
        self.avg_ms = ms if not self.runs else self.avg_ms * 0.9 + ms * 0.1  # среднее по последним кадрам
        self.runs += 1
        return found

    def detect_jpeg(self, data: bytes) -> list[DetectedObject]:
        with Image.open(io.BytesIO(data)) as img:
            return self.detect(img.convert("RGB"))

    def detect_rgb(self, data: bytes, width: int, height: int) -> list[DetectedObject]:
        return self.detect(Image.frombuffer("RGB", (width, height), data, "raw", "RGB", 0, 1))

    async def run(self, fn: Callable[..., list[DetectedObject]], *args: Any) -> list[DetectedObject]:
        """Выполнить разбор в потоке модели: кадры идут строго по очереди, цикл событий сервера не ждёт."""
        return await asyncio.get_running_loop().run_in_executor(self._executor, fn, *args)

    def _prepare(self, image: Image.Image) -> tuple[np.ndarray, float, int, int]:
        """Кадр → вход модели: вписать с сохранением пропорций, по краям серые поля, яркость 0–1."""
        scale = min(self.input_w / image.width, self.input_h / image.height)
        width, height = max(1, round(image.width * scale)), max(1, round(image.height * scale))
        left, top = (self.input_w - width) // 2, (self.input_h - height) // 2
        canvas = np.full((self.input_h, self.input_w, 3), PAD, dtype=np.uint8)
        canvas[top : top + height, left : left + width] = _resize(image.convert("RGB"), width, height)
        tensor = canvas.transpose(2, 0, 1)[None].astype(np.float32)
        tensor /= 255.0
        return tensor, scale, left, top

    def _parse(self, output: np.ndarray, width: int, height: int, scale: float, left: int, top: int) -> list[DetectedObject]:
        rows = np.asarray(output, dtype=np.float32)[0]
        if self.end2end if self.end2end is not None else rows.shape[1] == 6:
            # выгружена «без NMS» (end-to-end): строки [x1, y1, x2, y2, уверенность, класс]
            rows = rows[rows[:, 4] >= self.confidence]
            boxes, scores, classes = rows[:, :4], rows[:, 4], rows[:, 5].astype(np.int64)
        else:
            # обычный выход YOLO: [4 + число классов] × варианты — [cx, cy, w, h, уверенность по каждому классу]
            if rows.shape[0] == 4 + len(self.names) or rows.shape[0] < rows.shape[1]:
                rows = rows.T
            classes = rows[:, 4:].argmax(axis=1)
            scores = rows[np.arange(len(rows)), 4 + classes]
            keep = scores >= self.confidence
            rows, scores, classes = rows[keep], scores[keep], classes[keep]
            cx, cy, w, h = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
            boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)

        found = []
        # отбор общий для всех классов, и не из ТЗ тоже: бетононасос не должен превратиться в менее уверенный «самосвал»
        for i in _nms(boxes, scores, NMS_IOU):
            kind = self.classes.get(int(classes[i]))
            if kind is None:
                continue
            # из координат входа модели — в проценты исходного кадра (числа numpy не годятся для JSON — берём float)
            x1, y1, x2, y2 = (float(v) for v in boxes[i])
            x1, x2 = (min(max((v - left) / scale, 0.0), width) for v in (x1, x2))
            y1, y2 = (min(max((v - top) / scale, 0.0), height) for v in (y1, y2))
            box = clamp_box(x1 / width * 100, y1 / height * 100, (x2 - x1) / width * 100, (y2 - y1) / height * 100)
            found.append(DetectedObject(kind, round(float(scores[i]), 3), *box))
        return found


@lru_cache
def get_detector() -> Detector | None:
    """Своя модель, если файл на месте. Загружается один раз, при запуске сервера."""
    settings = get_settings()
    path = settings.detector_model
    if not path.is_file():
        if settings.analysis_provider == "local":
            raise RuntimeError(
                f"SK_ANALYSIS_PROVIDER=local, но файла модели нет: {path}. "
                "Положите модель (см. tools/export_model.py) или задайте путь в SK_DETECTOR_MODEL"
            )
        log.info("Своей модели нет (%s) — кадры разбирает демо-анализатор", path)
        return None
    try:
        detector = Detector.load(
            path,
            confidence=settings.detector_confidence,
            threads=settings.detector_threads,
            accelerate=settings.detector_accelerate,
            overrides=settings.detector_classes,
        )
    except Exception:
        if settings.analysis_provider == "local":
            raise
        # auto: битый файл или нет ONNX Runtime под эту систему — стенд всё равно должен подняться
        log.exception("Модель распознавания не загрузилась (%s) — кадры разбирает демо-анализатор", path)
        return None
    shown = ", ".join(f"{detector.names[i]} → {kind}" for i, kind in sorted(detector.classes.items()))
    quality = f", mAP50 {detector.map50}" if detector.map50 else ""
    log.info(
        "Модель распознавания: %s%s (%s), вход %d×%d, кадр ≈ %.0f мс; техника: %s",
        detector.name, quality, detector.device, detector.input_w, detector.input_h, detector.avg_ms, shown,
    )  # fmt: skip
    if detector.ignored:
        log.info("Классы модели не из списка техники — рамки не показываем: %s", ", ".join(detector.ignored))
    if not detector.classes:
        log.warning("Ни один класс модели не совпал с техникой ТЗ — задайте соответствие в SK_DETECTOR_CLASSES")
    return detector
