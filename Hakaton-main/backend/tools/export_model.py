"""Новая версия модели распознавания: файл .pt от Ultralytics → models/detector.onnx для сервера.

Сервер считает модель через ONNX Runtime, без PyTorch и Ultralytics (это гигабайты). Перевести .pt в ONNX нужно
один раз; Ultralytics с PyTorch ставятся только на время запуска:

    cd backend
    uv run --with "ultralytics>=8.4" --with onnx --with onnxslim python -m tools.export_model ~/Downloads/best.pt

Потом перезапустите сервер. Что делает:
  • выход модели — все варианты рамок (NMS делает сервер), как у Ultralytics по умолчанию: на нём модель и
    показала свою точность. Выход YOLO26 «без NMS» (nms=False) у дообученных моделей бывает заметно хуже;
  • вход модели — 640×384: кадры у нас 16:9, в квадрате 640×640 почти половина пикселей ушла бы на поля.
    Масштаб кадра тот же, что при обучении на 640, — точность та же, а считается в полтора раза быстрее;
  • веса хранятся во float16, как и в самом .pt: файл вдвое меньше. Считает сервер во float32 — при загрузке
    ONNX Runtime сам разворачивает веса обратно;
  • в файл записываются классы, название и точность модели — их видно в журнале сервера и в /api/tracker/status;
  • сверяет ответы получившегося файла с исходной моделью на демо-фото.
"""

import argparse
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DEFAULT_OUT = BACKEND / "models" / "detector.onnx"
SAMPLES = BACKEND / "app" / "assets" / "seed"


def store_half(model, min_size: int = 1024) -> int:  # noqa: ANN001 — onnx.ModelProto
    """Большие веса float32 → хранить во float16 + узел Cast обратно во float32. Возвращает, сколько весов сжато.

    ONNX Runtime при загрузке сворачивает такие Cast в обычные веса float32 — скорость и точность те же.
    """
    import numpy as np
    from onnx import TensorProto, helper, numpy_helper

    graph, casts = model.graph, []
    for init in graph.initializer:
        if init.data_type != TensorProto.FLOAT:
            continue
        weights = numpy_helper.to_array(init)
        if weights.size < min_size:
            continue
        name = init.name
        init.CopyFrom(numpy_helper.from_array(weights.astype(np.float16), f"{name}__fp16"))
        casts.append(helper.make_node("Cast", [f"{name}__fp16"], [name], to=TensorProto.FLOAT, name=f"{name}__cast"))
    nodes = casts + list(graph.node)  # Cast — раньше всех узлов, которым нужны эти веса
    del graph.node[:]
    graph.node.extend(nodes)
    return len(casts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Перевести модель YOLO (.pt, Ultralytics) в ONNX для сервера")
    parser.add_argument("weights", type=Path, help="файл модели .pt")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"куда положить (по умолчанию {DEFAULT_OUT})")
    parser.add_argument("--title", help="название модели для журнала и интерфейса (по умолчанию — имя файла)")
    parser.add_argument("--height", type=int, default=384, help="высота входа модели (ширина — 640)")
    args = parser.parse_args()

    import onnx
    from onnx import helper
    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    checkpoint = getattr(model, "ckpt", None) or {}
    metrics = checkpoint.get("train_metrics") or {}
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / args.weights.name
        source.write_bytes(args.weights.read_bytes())  # экспорт пишет рядом с .pt — не мусорим в папке пользователя
        exported = Path(
            YOLO(str(source)).export(format="onnx", imgsz=(args.height, 640), dynamic=False, simplify=True, device="cpu")
        )
        onnx_model = onnx.load(str(exported))

    compressed = store_half(onnx_model)
    extra = {
        "title": args.title or args.weights.stem,
        "source": args.weights.name,
        "trained": str(checkpoint.get("date") or "")[:10],
        "map50": f"{metrics['metrics/mAP50(B)']:.3f}" if "metrics/mAP50(B)" in metrics else "",
        "map50_95": f"{metrics['metrics/mAP50-95(B)']:.3f}" if "metrics/mAP50-95(B)" in metrics else "",
    }
    helper.set_model_props(
        onnx_model, {**{p.key: p.value for p in onnx_model.metadata_props}, **{k: v for k, v in extra.items() if v}}
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(onnx_model, str(args.out))
    size_mb = args.out.stat().st_size / 1e6
    print(f"Готово: {args.out} ({size_mb:.1f} МБ, весов во float16: {compressed}), вход 640×{args.height}")
    print(f"Классы: {model.names}")
    compare(model, args.out, args.height)


def compare(model, path: Path, height: int) -> None:  # noqa: ANN001 — ultralytics.YOLO
    """Исходная модель и получившийся файл (тем же кодом, что на сервере) на демо-фото: техника должна совпасть."""
    sys.path.insert(0, str(BACKEND))
    import onnxruntime as ort
    from PIL import Image

    from app.services.detector import Detector, class_map

    detector = Detector(ort.InferenceSession(str(path)), name=path.stem, confidence=0.35)
    mapping = class_map(model.names)
    mismatches = 0
    for photo in sorted(SAMPLES.glob("*.jpg")):
        image = Image.open(photo).convert("RGB")
        ours = sorted(d.type for d in detector.detect(image))
        # отбор рамок у сервера общий для всех классов (одна машина — одна рамка) — у Ultralytics так же: agnostic_nms
        result = model.predict(image, imgsz=(height, 640), conf=0.35, iou=0.7, agnostic_nms=True, verbose=False)[0]
        theirs = sorted(mapping[int(c)] for c in result.boxes.cls.tolist() if int(c) in mapping)
        mark = "совпало" if ours == theirs else "РАЗНИЦА"
        mismatches += ours != theirs
        print(f"  {photo.stem:<24} {mark:<8} сервер: {', '.join(ours) or '—'}; ultralytics: {', '.join(theirs) or '—'}")
    print("Ответы совпали на всех фото" if not mismatches else f"Разница на {mismatches} фото — проверьте модель")


if __name__ == "__main__":
    main()
