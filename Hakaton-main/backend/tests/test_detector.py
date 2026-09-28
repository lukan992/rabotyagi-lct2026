"""Своя модель распознавания: классы модели → техника ТЗ, разбор выхода YOLO, номера машин, рамки по видео."""

import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.config import ASSETS_DIR, BASE_DIR, Settings, get_settings
from app.services import detector as detector_module
from app.services.analysis.base import DetectedObject
from app.services.detector import Detector, _resize, class_map
from app.services.realtime import FORGET_S, BoxTracker, LiveTracking

settings = get_settings()
BACKEND = BASE_DIR
MOCS = {
    0: "Worker", 1: "Static crane", 2: "Hanging head", 3: "Crane", 4: "Roller", 5: "Bulldozer", 6: "Excavator",
    7: "Truck", 8: "Loader", 9: "Pump truck", 10: "Concrete mixer", 11: "Pile driving", 12: "Other vehicle",
}  # fmt: skip


class FakeSession:
    """Вместо ONNX Runtime: отдаёт заданный выход модели и запоминает, что ей подали."""

    def __init__(self, output: np.ndarray, end2end: bool | None = None) -> None:
        self.output, self.inputs = output, []
        self.meta = {"names": repr(MOCS), "title": "Тестовая модель"}
        if end2end is not None:
            self.meta["end2end"] = str(end2end)  # так пишет Ultralytics при выгрузке

    def get_modelmeta(self) -> SimpleNamespace:
        return SimpleNamespace(custom_metadata_map=self.meta)

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="images", shape=[1, 3, 384, 640])]

    def get_providers(self) -> list[str]:
        return ["CPUExecutionProvider"]

    def run(self, _outputs: None, feed: dict) -> list[np.ndarray]:
        self.inputs.append(feed["images"])
        return [self.output]


def _raw(*rows: tuple) -> np.ndarray:
    """Обычный выход YOLO: [4 + 13 классов] × варианты; rows — (cx, cy, w, h, класс, уверенность) во входе 640×384."""
    out = np.zeros((1, 4 + len(MOCS), len(rows)), np.float32)
    for i, (cx, cy, w, h, cls, conf) in enumerate(rows):
        out[0, :4, i] = cx, cy, w, h
        out[0, 4 + cls, i] = conf
    return out


def test_model_classes_map_to_equipment():
    # MOCS: люди, башенные краны, погрузчики — не техника ТЗ; Truck там — самосвалы
    assert class_map(MOCS) == {3: "crane", 4: "roller", 5: "bulldozer", 6: "excavator", 7: "dump_truck", 10: "mixer"}
    # поправки из настроек важнее: Truck — грузовик, бетононасос — как бетоносмеситель, каток не показывать
    fixed = class_map(MOCS, {"Truck": "truck", "pump truck": "mixer", "Roller": ""})
    assert fixed[7] == "truck" and fixed[9] == "mixer" and 4 not in fixed
    # модель различает самосвал и грузовик — берём как есть; наши имена и русские названия подходят сами
    names = {0: "dump_truck", 1: "Truck", 2: "Кран-манипулятор", 3: "Concrete-Mixer"}
    assert class_map(names) == {0: "dump_truck", 1: "truck", 2: "manipulator", 3: "mixer"}
    with pytest.raises(ValueError, match="неизвестные типы техники"):
        Settings(detector_classes={"Truck": "lorry"})


def test_model_output_becomes_equipment_boxes():
    # кадр 1280×720 → вход 640×384: вдвое меньше, сверху и снизу по 12 px серых полей
    session = FakeSession(
        _raw(
            (320, 192, 320, 180, 6, 0.9),  # экскаватор: половина кадра по центру
            (322, 194, 316, 176, 12, 0.5),  # та же машина «прочим транспортом», менее уверенно — одна машина, одна рамка
            (100, 100, 40, 40, 0, 0.95),  # человек — не техника
            (500, 300, 60, 40, 7, 0.2),  # самосвал, но слишком неуверенно
            (560, 100, 80, 60, 9, 0.8),  # бетононасос (не техника ТЗ)…
            (562, 102, 78, 58, 7, 0.6),  # …его же модель сочла и самосвалом: не превращаем бетононасос в самосвал
        ),
        end2end=False,  # вариантов рамок всего 6 — по форме ответа это не отличить от выхода «без NMS»
    )
    detector = Detector(session, name="test", confidence=0.35)
    assert detector.detect(Image.new("RGB", (1280, 720), "white")) == [DetectedObject("excavator", 0.9, 25.0, 25.0, 50.0, 50.0)]
    tensor = session.inputs[0]
    assert tensor.shape == (1, 3, 384, 640)
    assert tensor[0, 0, 0, 0] == pytest.approx(114 / 255) and tensor[0, 0, 12, 0] == 1.0  # поля серые, кадр — с 12-й строки
    assert detector.name == "Тестовая модель" and detector.frame_size == (640, 360)


def test_end_to_end_output_and_non_16_9_frames():
    rows = np.zeros((1, 300, 6), np.float32)  # выгрузка «без NMS»: [x1, y1, x2, y2, уверенность, класс]; формат — по форме
    rows[0, 0] = [64, 0, 576, 384, 0.8, 4]  # каток на весь кадр 4:3 (он вписан во вход 512×384 с полями по бокам)
    detector = Detector(FakeSession(rows), name="test", confidence=0.35)
    assert detector.detect(Image.new("RGB", (1600, 1200))) == [DetectedObject("roller", 0.8, 0.0, 0.0, 100.0, 100.0)]


def test_frames_are_shrunk_like_opencv():
    # вдвое меньше — среднее по квадрату 2×2 с округлением, как cv2.resize(INTER_LINEAR) при ровно двукратном уменьшении
    pixels = np.array([[[0, 10, 255], [1, 11, 255], [8, 0, 0], [8, 0, 0]], [[2, 12, 255], [4, 13, 254], [8, 0, 0], [9, 0, 0]]])
    shrunk = _resize(Image.fromarray(pixels.astype(np.uint8)), 2, 1)
    assert shrunk.tolist() == [[[2, 12, 255], [8, 0, 0]]]


def _det(kind: str, x: float, y: float = 40.0, w: float = 20.0) -> DetectedObject:
    return DetectedObject(kind, 0.9, x, y, w, 20.0)


def _ids(objects: list[dict]) -> dict[str, float]:
    return {o["trackId"]: o["box"]["x"] for o in objects}


def test_tracker_keeps_machine_numbers():
    tracker = BoxTracker()
    assert _ids(tracker.update([_det("excavator", 10), _det("dump_truck", 60)], now=0.0)) == {
        "excavator:1": 10,
        "dump_truck:2": 60,
    }
    # порядок рамок в кадре не важен; самосвал проехал 8 % кадра — перекрытие большое, номер тот же
    assert _ids(tracker.update([_det("dump_truck", 68), _det("excavator", 11)], now=0.2)) == {
        "excavator:1": 11,
        "dump_truck:2": 68,
    }
    # кадры редкие (анализ раз в 2 с): самосвал уехал на 17 % при длине 20 % — почти не перекрывается, но центр рядом
    assert _ids(tracker.update([_det("excavator", 11), _det("dump_truck", 85)], now=2.2))["dump_truck:2"] == 85
    # на том же месте — другой тип: это другая машина; самосвал на кадре не нашёлся — рамку ещё держим
    step = tracker.update([_det("excavator", 11), _det("mixer", 85)], now=2.4)
    assert _ids(step) == {"excavator:1": 11, "dump_truck:2": 85, "mixer:3": 85}
    assert "dump_truck:2" not in _ids(tracker.update([_det("excavator", 11)], now=2.6))  # не нашёлся второй раз — прячем
    # давно не видно — трек забыт: вернулся — уже с новым номером
    assert _ids(tracker.update([_det("dump_truck", 85)], now=2.6 + FORGET_S + 1)) == {"dump_truck:4": 85}


@pytest.mark.anyio
async def test_live_boxes_from_frame_analysis_until_video_is_read():
    delivered: list[dict] = []
    tracking = LiveTracking(detector=None, deliver=delivered.append, watched=set)  # видео в тесте не читается
    at = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    tracking.feed("c1", at, [_det("excavator", 10)])
    assert delivered == [
        {
            "cameraId": "c1",
            "ts": "2026-09-25T10:00:00+00:00",
            "objects": [
                {
                    "trackId": "excavator:1",
                    "type": "excavator",
                    "confidence": 0.9,
                    "box": {"x": 10, "y": 40.0, "w": 20.0, "h": 20.0},
                }
            ],
        }
    ]
    tracking._video_at["c1"] = asyncio.get_running_loop().time()  # видео камеры разбирается — рамки и так идут чаще
    tracking.feed("c1", at, [_det("excavator", 12)])
    assert len(delivered) == 1


@pytest.mark.anyio
async def test_model_takes_freshest_frame_of_each_camera_in_turn():
    delivered: list[dict] = []
    seen: list[bytes] = []

    class FakeDetector:
        frame_size = (640, 360)

        async def run(self, fn, *args):  # noqa: ANN001, ANN202
            return fn(*args)

        def detect_rgb(self, data: bytes, width: int, height: int) -> list[DetectedObject]:
            seen.append(data)
            return [_det("excavator", 10)]

    tracking = LiveTracking(FakeDetector(), delivered.append, set)
    idle = asyncio.create_task(asyncio.sleep(3600))
    tracking._readers = {"c1": idle, "c2": idle}  # видео «читается» у двух камер
    at = datetime.now(UTC)
    for seq, (camera, frame) in enumerate([("c1", b"c1-old"), ("c1", b"c1-new"), ("c2", b"c2")], 1):
        tracking._frames[camera] = (seq, at, frame)  # у c1 старый кадр успел смениться новым — старый не нужен
    tracking._new_frame.set()
    worker = asyncio.create_task(tracking._infer())
    for _ in range(20):
        await asyncio.sleep(0)
    worker.cancel()
    idle.cancel()
    assert sorted(seen) == [b"c1-new", b"c2"] and {m["cameraId"] for m in delivered} == {"c1", "c2"}
    # кадр для модели — как на анализе: 1280×720 с полями, потом вдвое меньше средним по 2×2 уже в RGB
    video_filter = tracking._command("c1")[tracking._command("c1").index("-vf") + 1]
    assert video_filter.startswith(f"fps={settings.realtime_fps:g},") and video_filter.endswith(
        "format=rgb24,scale=640:360:flags=area"
    )


def test_modules_import_in_any_order():
    # модуль модели импортирует пакет analysis, а тот — анализатор на модели: круг ловится только в чистом процессе
    for module in ("app.services.detector", "app.services.realtime", "app.services.analysis.local", "tools.export_model"):
        result = subprocess.run([sys.executable, "-c", f"import {module}"], cwd=BACKEND, capture_output=True, text=True)
        assert result.returncode == 0, f"{module}: {result.stderr.strip().splitlines()[-1]}"


def test_local_provider_needs_the_model_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "detector_model", tmp_path / "missing.onnx")
    try:
        monkeypatch.setattr(settings, "analysis_provider", "local")
        detector_module.get_detector.cache_clear()
        with pytest.raises(RuntimeError, match="файла модели нет"):
            detector_module.get_detector()
        monkeypatch.setattr(settings, "analysis_provider", "auto")  # auto без модели — демо-анализатор
        detector_module.get_detector.cache_clear()
        assert detector_module.get_detector() is None
        broken = tmp_path / "broken.onnx"  # и с битым файлом: стенд должен подняться
        broken.write_bytes(b"not a model")
        monkeypatch.setattr(settings, "detector_model", broken)
        detector_module.get_detector.cache_clear()
        assert detector_module.get_detector() is None
    finally:
        detector_module.get_detector.cache_clear()


@pytest.mark.skipif(not settings.detector_model.is_file(), reason="нет файла модели (models/detector.onnx)")
def test_real_model_finds_equipment_on_demo_photos():
    detector = Detector.load(settings.detector_model, confidence=0.35, accelerate=False)
    seed = ASSETS_DIR / "seed"
    found = {
        name: [d.type for d in detector.detect_jpeg((seed / f"{name}.jpg").read_bytes())]
        for name in ("pit-loading", "yard-bulldozer", "foundation-mixers")
    }
    assert {"excavator", "dump_truck"} <= set(found["pit-loading"])
    assert "bulldozer" in found["yard-bulldozer"] and found["foundation-mixers"].count("mixer") == 2
