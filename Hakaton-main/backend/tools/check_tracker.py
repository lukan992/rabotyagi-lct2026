"""Проверка сервиса разметки перед подключением к СтройКонтролю.

Подключается к WebSocket сервиса так же, как это делает сервер СтройКонтроля (заголовок Authorization: Bearer <ключ>),
слушает поток несколько секунд и проверяет его по контракту: формат сообщений, рамки в процентах, track_id,
частоту по камерам, время кадров. В конце — понятный список того, что исправить.

Запуск из папки backend (или где угодно, где есть пакет websockets):
    uv run python -m tools.check_tracker ws://127.0.0.1:8200/stream --key dev-tracker-key
    ... --seconds 20 --backend http://192.168.1.10:8100    # сверить id камер со списком сервера (GET /api/tracker/cameras)
Без репозитория: uv run --with websockets python check_tracker.py …
"""

import argparse
import asyncio
import json
import math
import sys
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime

import websockets

TYPES = {"excavator", "dump_truck", "roller", "manipulator", "mixer", "bulldozer", "truck", "crane"}
MIN_FPS, GOOD_FPS = 5.0, 10.0


class Report:
    def __init__(self) -> None:
        self.problems: dict[str, int] = defaultdict(int)  # текст → сколько раз
        self.examples: dict[str, str] = {}

    def add(self, text: str, example: object) -> None:
        self.problems[text] += 1
        self.examples.setdefault(text, json.dumps(example, ensure_ascii=False, default=str)[:300])


def check_object(obj: object, report: Report) -> tuple[str, str] | None:
    """(камера-независимо) → (track_id, тип) для статистики треков; None — объект не годится."""
    if not isinstance(obj, dict):
        report.add("объект — не JSON-объект", obj)
        return None
    kind = obj.get("type")
    if kind not in TYPES:
        report.add(f"тип «{kind}» не из списка — сервер такие объекты пропускает", obj)
        return None
    track = obj.get("track_id")
    if track is None or track == "":
        report.add("нет track_id — рамки не смогут плавно ехать за машиной", obj)
    confidence = obj.get("confidence")
    if not isinstance(confidence, int | float) or not 0 <= confidence <= 1:
        report.add("confidence не доля 0–1 (проценты? строка?)", obj)
    box = obj.get("box")
    if not isinstance(box, dict) or not all(isinstance(box.get(k), int | float) for k in "xywh"):
        report.add('box должен быть {"x", "y", "w", "h"} с числами', obj)
        return None
    x, y, w, h = (float(box[k]) for k in "xywh")
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        report.add("рамка без размера", obj)
    elif max(x, y, w, h) > 100.5:
        report.add("рамка больше 100 — похоже на пиксели; нужны проценты от кадра 0–100", obj)
    elif max(x, y, w, h) <= 1:
        report.add("рамка в долях 0–1 (как у YOLO) — нужны проценты 0–100, x и y — левый верхний угол", obj)
    elif x + w > 101 or y + h > 101:
        report.add("рамка выходит за кадр — возможно, x/y это центр, а нужен левый верхний угол", obj)
    return (str(track), kind) if track not in (None, "") else None


def check_message(raw: str, report: Report, stats: dict) -> None:
    try:
        msg = json.loads(raw)
    except ValueError:
        report.add("сообщение — не JSON", raw)
        return
    if not isinstance(msg, dict) or not isinstance(msg.get("camera_id"), str) or not isinstance(msg.get("objects"), list):
        report.add('сообщение должно быть {"camera_id": "…", "ts": "…", "objects": [...]}', msg)
        return
    camera = msg["camera_id"]
    cam = stats[camera]
    cam["messages"] += 1
    size = (msg.get("frame_w"), msg.get("frame_h"))
    if all(isinstance(v, int | float) and v > 0 for v in size):
        cam["size"] = f"{size[0]}×{size[1]}"
    if not msg["objects"]:
        cam["empty"] += 1
    ts = msg.get("ts")
    try:
        taken = datetime.fromisoformat(ts)
        if taken.tzinfo is None:
            report.add("ts без часового пояса — нужен ISO 8601 с поясом, например 2026-09-25T10:15:03.120+03:00", msg)
        else:
            age = (datetime.now(UTC) - taken).total_seconds()
            cam["ages"].append(age)
    except (TypeError, ValueError):
        report.add("ts отсутствует или не ISO 8601 — нужно время кадра с часовым поясом", msg)
    in_frame = set()
    for obj in msg["objects"]:
        key = check_object(obj, report)
        if key:
            if key in in_frame:  # сервер различает треки по паре (тип, track_id) — вторую рамку он отбросит
                report.add("два объекта одного типа с одним track_id в одном кадре", msg)
            in_frame.add(key)
            cam["tracks"][key] += 1


async def listen(url: str, key: str, seconds: float, report: Report, stats: dict) -> str | None:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with websockets.connect(url, additional_headers=headers, open_timeout=10) as ws:
            loop = asyncio.get_running_loop()
            end = loop.time() + seconds
            while (left := end - loop.time()) > 0:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=left)
                except TimeoutError:
                    break
                check_message(raw if isinstance(raw, str) else raw.decode(errors="replace"), report, stats)
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        return f"не удалось подключиться к {url}: {exc}"
    return None


def backend_cameras(backend: str, key: str) -> set[str] | None:
    request = urllib.request.Request(f"{backend.rstrip('/')}/api/tracker/cameras", headers={"X-Api-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return {c["id"] for c in json.load(response)}
    except Exception as exc:  # noqa: BLE001
        print(f"  ! список камер с сервера не получен: {exc}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка потока рамок сервиса разметки по контракту СтройКонтроля")
    parser.add_argument("url", help="WebSocket сервиса, например ws://127.0.0.1:8200/stream")
    parser.add_argument("--key", default="", help="ключ сервиса (SK_TRACKER_API_KEY)")
    parser.add_argument("--seconds", type=float, default=15, help="сколько слушать поток")
    parser.add_argument("--backend", help="адрес сервера СтройКонтроля — сверить id камер")
    args = parser.parse_args()

    report, stats = (
        Report(),
        defaultdict(lambda: {"messages": 0, "empty": 0, "ages": [], "tracks": defaultdict(int), "size": None}),
    )
    print(f"Слушаю {args.url} {args.seconds:.0f} с…")
    if error := asyncio.run(listen(args.url, args.key, args.seconds, report, stats)):
        print(f"✗ {error}\n  Проверьте адрес, что сервис запущен и принимает заголовок Authorization: Bearer <ключ>.")
        return 2

    if not stats:
        report.add("за всё время не пришло ни одного сообщения по камерам", "")
    print(f"\nКамеры ({len(stats)}):")
    for camera, cam in sorted(stats.items()):
        fps = cam["messages"] / args.seconds
        tracks = cam["tracks"]
        # трек «живёт» в среднем столько сообщений; меньше пары — id меняются каждый кадр
        per_track = (sum(tracks.values()) / len(tracks)) if tracks else 0
        ages = sorted(cam["ages"])
        lag = f", задержка кадра ~{ages[len(ages) // 2] * 1000:.0f} мс" if ages else ""
        size = f", кадр {cam['size']}" if cam["size"] else ", размер кадра не указан (считаем 16:9)"
        print(f"  {camera}: {cam['messages']} сообщ. ({fps:.1f} в с), пустых {cam['empty']}, треков {len(tracks)}{lag}{size}")
        if fps < MIN_FPS:
            report.add(f"камера {camera}: {fps:.1f} сообщ./с — мало для плавных рамок, нужно {GOOD_FPS:.0f}–15", "")
        if tracks and per_track < 3 and cam["messages"] >= 10:
            report.add(
                f"камера {camera}: track_id меняются почти каждый кадр — трекер не держит машину",
                [t for t, _ in list(tracks)[:6]],
            )
        if ages and ages[len(ages) // 2] < -1:
            report.add(f"камера {camera}: ts в будущем — часы сервиса спешат или время не в UTC/без пояса", "")
        if ages and ages[len(ages) // 2] > 3:
            report.add(f"камера {camera}: кадры старше 3 с — сервис не успевает или ts не время кадра", "")

    if args.backend:
        known = backend_cameras(args.backend, args.key)
        if known is not None:
            unknown = set(stats) - known
            if unknown:
                report.add(
                    f"камеры {', '.join(sorted(unknown))} — нет в списке сервера; camera_id должен быть id из GET /api/tracker/cameras",
                    "",
                )
            silent = known - set(stats)
            if silent:
                print(f"  ⓘ по камерам {', '.join(sorted(silent))} сообщений не было (выключены, без видео или не обработаны)")

    if not report.problems:
        print("\n✓ Поток соответствует контракту: можно подключать к серверу (SK_TRACKER_URL).")
        return 0
    print("\n✗ Что исправить:")
    for text, count in sorted(report.problems.items(), key=lambda item: -item[1]):
        example = f"\n      пример: {report.examples[text]}" if report.examples[text] not in ('""', "") else ""
        print(f"  • {text} (×{count}){example}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
