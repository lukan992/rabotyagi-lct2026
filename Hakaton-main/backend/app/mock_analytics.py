"""Имитация двух сервисов аналитики коллеги (deterministic и vlm_llm) по контракту frame-analysis-v1 — для демо,
тестов и e2e без Docker (настоящие сервисы — комплект коллеги 0.2.0, см. README бэкенда). Один процесс, два адреса:

    uv run uvicorn app.mock_analytics:app --port 8300
    DETERMINISTIC_SERVICE_URL=http://127.0.0.1:8300/deterministic
    VLM_LLM_SERVICE_URL=http://127.0.0.1:8300/vlm_llm

Запрос проверяется строго по контракту — так имитация ловит ошибки нашего адаптера: ровно две части multipart,
заголовки, sha256 и размеры кадра, классы и рамки, план (конкретные работы справочника, сроки, порядок), история
(окно, повторы кадров, отметки выполнения), идемпотентность по request_id. Отвечает правдоподобно, но без моделей:
«по технике» — по упрощённой матрице «класс техники → вид работ», с переходом к следующей работе и сроками
(schedule-rules-v1); «по снимку» — те же группы от лица VLM: визуальные наблюдения строятся из рамок CV, картинку
имитация не смотрит. Справочник — тот же, что у сервисов коллеги 0.2.0 (assets/analytics/catalog.json), роли техники —
выжимка их матрицы v2 (matrix-roles.json): план, сопоставленный для имитации, годится и для настоящих сервисов.
Пункты плана — работы concrete и no_class (без техники: только сроки и отметки, в кандидаты по кадру не попадают;
план только из них — insufficient_evidence); сводные этапы summary — отказ unknown_stage.

MOCK_ANALYTICS_TOKEN — требовать этот Bearer (по умолчанию ANALYTICS_SERVICE_TOKEN, пусто — без проверки);
MOCK_VLM_DELAY_S — сколько «думает» сервис по снимку (2 с); MOCK_ANALYTICS_FAULTS=vlm_llm=model_failure — отвечать
этой ошибкой (проверить, как интерфейс показывает отказ).
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image
from starlette.datastructures import UploadFile

SERVICE_VERSION = "mock-0.2.0"
TOKEN = os.environ.get("MOCK_ANALYTICS_TOKEN", os.environ.get("ANALYTICS_SERVICE_TOKEN", ""))
DELAYS_S = {"deterministic": 0.2, "vlm_llm": float(os.environ.get("MOCK_VLM_DELAY_S", "2"))}
FAULTS: dict[str, str] = dict(
    item.split("=", 1) for item in os.environ.get("MOCK_ANALYTICS_FAULTS", "").split(",") if "=" in item
)

LIMITS = {
    "image_max_bytes": 25_000_000,
    "metadata_max_bytes": 1_000_000,
    "image_max_side": 8192,
    "image_max_pixels": 40_000_000,
    "detections_max": 200,
    "plan_steps_max": 500,
    "history_observations_max": 500,
    "progress_events_max": 1000,
    "model_context_max_chars": 200_000,
    "idempotency_retention_seconds": 7 * 24 * 3600,
}

# ---------- справочник и матрица — из комплекта коллеги 0.2.0 ----------
ASSETS = Path(__file__).resolve().parent / "assets" / "analytics"
CATALOG = json.loads((ASSETS / "catalog.json").read_text(encoding="utf-8"))
CATALOG_VERSION = CATALOG["catalog_version"]
_MATRIX = json.loads((ASSETS / "matrix-roles.json").read_text(encoding="utf-8"))
MATRIX_VERSION = _MATRIX["matrix_version"]
PROFILES_VERSION = "visual-profiles-708cb01523c740f39b8789aa"  # визуальные профили работ у сервиса по снимку (compose коллеги)
CLASS_NAMES = {c["code"]: c["name_ru"] for c in CATALOG["equipment_classes"]}
WORK = {w["stage_id"]: w for w in CATALOG["work_stages"]}
# вид работ → класс техники → вес роли (1–3) и вес сигнала текущей активности (0–3)
ROLES = {int(sid): {code: weights[0] for code, weights in classes.items()} for sid, classes in _MATRIX["roles"].items()}
SIGNALS = {int(sid): {code: weights[1] for code, weights in classes.items()} for sid, classes in _MATRIX["roles"].items()}
CLASS_GROUPS = _MATRIX["class_groups"]  # класс → группа свидетельств (earthmoving, logistics…)

# ---------- проверка запроса ----------
INPUT_KEYS = {
    "schema_version",
    "request_id",
    "site_id",
    "object_type_code",
    "catalog_version",
    "frame",
    "scope",
    "cv",
    "plan",
    "history",
}
FRAME_KEYS = {"image_id", "camera_id", "observed_at", "source_ref", "image_sha256", "media_type", "width", "height"}
STEP_KEYS = {"step_key", "sequence_no", "stage_id", "planned_start_at", "planned_end_at"}
OBSERVATION_KEYS = {"observation_id", "image_id", "camera_id", "observed_at", "source_ref", "image_sha256", "cv"}
EVENT_KEYS = {"event_id", "step_key", "stage_id", "plan_revision_id", "state", "effective_at", "recorded_at", "source_ref",
              "actual_started_at", "actual_completed_at"}  # fmt: skip
OBJECT_TYPES = {"housing", "education", "healthcare", "sports", "culture", "administrative", "preschool", "office", "roads"}
MEDIA = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
SHA = re.compile(r"^[0-9a-f]{64}$")


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, path: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.path, self.retryable = status, code, message, path, retryable


def _invalid(path: str, message: str, code: str = "invalid_context", status: int = 422) -> Problem:
    return Problem(status, code, message, path)


def _keys(value: Any, keys: set[str], path: str) -> dict:
    if not isinstance(value, dict):
        raise _invalid(path, "нужен объект")
    if missing := keys - value.keys():
        raise _invalid(path, f"нет полей {sorted(missing)} (отсутствие ключа не равно null)")
    if extra := value.keys() - keys:
        raise _invalid(path, f"лишние поля {sorted(extra)}")
    return value


def _id(value: Any, path: str, *, nullable: bool = False) -> Any:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 100 or value != value.strip() or not value.strip():
        raise _invalid(path, "ID — непустая строка 1–100 символов без пробелов по краям")
    return value


def _time(value: Any, path: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not RFC3339.match(value):
        raise _invalid(path, "время RFC 3339 с явным поясом, например 2026-09-26T12:00:00+03:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise _invalid(path, "несуществующая дата") from None


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _bbox(value: Any, path: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 4 or not all(_number(v) and 0 <= v <= 1 for v in value):
        raise _invalid(path, "bbox — четыре числа от 0 до 1: [x_min, y_min, x_max, y_max]")
    if not (value[0] < value[2] and value[1] < value[3]):
        raise _invalid(path, "нужно x_min < x_max и y_min < y_max")
    return value


def _cv(value: Any, path: str, roi: list | None) -> dict:
    cv = _keys(value, {"status", "source_ref", "model_version", "detections"}, path)
    if cv["status"] not in ("ok", "unavailable", "failed"):
        raise _invalid(f"{path}/status", "status — ok, unavailable или failed")
    detections = cv["detections"]
    if not isinstance(detections, list):
        raise _invalid(f"{path}/detections", "нужен список")
    if cv["status"] == "ok":
        _id(cv["source_ref"], f"{path}/source_ref")
    elif detections:
        raise _invalid(f"{path}/detections", "при недоступном CV детекций быть не может")
    if len(detections) > LIMITS["detections_max"]:
        raise Problem(413, "payload_too_large", f"больше {LIMITS['detections_max']} детекций", f"{path}/detections")
    seen = set()
    for n, d in enumerate(detections):
        where = f"{path}/detections/{n}"
        _keys(d, {"detection_id", "class_code", "bbox", "confidence"}, where)
        if _id(d["detection_id"], f"{where}/detection_id") in seen:
            raise _invalid(f"{where}/detection_id", "detection_id повторяется в кадре")
        seen.add(d["detection_id"])
        if d["class_code"] not in CLASS_NAMES:
            raise _invalid(f"{where}/class_code", f"класса {d['class_code']!r} нет в справочнике", "unknown_class")
        box = _bbox(d["bbox"], f"{where}/bbox")
        if d["confidence"] is not None and not (_number(d["confidence"]) and 0 <= d["confidence"] <= 1):
            raise _invalid(f"{where}/confidence", "confidence — число от 0 до 1 или null")
        if roi and not (roi[0] <= (box[0] + box[2]) / 2 <= roi[2] and roi[1] <= (box[1] + box[3]) / 2 <= roi[3]):
            raise _invalid(f"{where}/bbox", "центр рамки вне области участка (roi_bbox)")
    return cv


def validate(meta: Any, image: bytes, media_type: str, plans: dict) -> None:
    _keys(meta, INPUT_KEYS, "")
    if meta["schema_version"] != "frame-analysis-input-v1":
        raise _invalid("/schema_version", "нужен frame-analysis-input-v1", "invalid_request", 400)
    _id(meta["request_id"], "/request_id")
    _id(meta["site_id"], "/site_id")
    if meta["object_type_code"] is not None and meta["object_type_code"] not in OBJECT_TYPES:
        raise _invalid("/object_type_code", "неизвестный тип объекта — передайте null")
    if meta["catalog_version"] != CATALOG_VERSION:
        raise Problem(409, "catalog_version_mismatch", f"справочник сервиса — версии {CATALOG_VERSION}", "/catalog_version")

    frame = _keys(meta["frame"], FRAME_KEYS, "/frame")
    for key in ("image_id", "camera_id", "source_ref"):
        _id(frame[key], f"/frame/{key}")
    observed = _time(frame["observed_at"], "/frame/observed_at")
    if frame["media_type"] != media_type or media_type not in MEDIA:
        raise Problem(415, "unsupported_media_type", "media_type кадра не совпадает с частью image", "/frame/media_type")
    if not isinstance(frame["image_sha256"], str) or not SHA.match(frame["image_sha256"]):
        raise _invalid("/frame/image_sha256", "64 строчных hex-символа")
    if hashlib.sha256(image).hexdigest() != frame["image_sha256"]:
        raise _invalid("/frame/image_sha256", "sha256 не совпадает с байтами кадра", "image_hash_mismatch")
    try:
        with Image.open(BytesIO(image)) as picture:
            fmt, size, frames = picture.format, picture.size, getattr(picture, "n_frames", 1)
            picture.load()
    except Exception:  # noqa: BLE001
        raise _invalid("/frame", "кадр повреждён", "invalid_image") from None
    if MEDIA[media_type] != fmt or frames != 1 or size != (frame["width"], frame["height"]):
        raise _invalid(
            "/frame", f"кадр {fmt} {size[0]}×{size[1]}, а в metadata — {frame['width']}×{frame['height']}", "invalid_image"
        )

    scope = _keys(meta["scope"], {"plan_stream_code", "roi_bbox", "source_ref"}, "/scope")
    roi = None
    if scope["plan_stream_code"] is None:
        if scope["roi_bbox"] is not None or scope["source_ref"] is not None or meta["plan"] is not None:
            raise _invalid("/scope", "участок неизвестен — все поля scope и plan равны null")
    else:
        _id(scope["plan_stream_code"], "/scope/plan_stream_code")
        _id(scope["source_ref"], "/scope/source_ref")
        roi = None if scope["roi_bbox"] is None else _bbox(scope["roi_bbox"], "/scope/roi_bbox")
    _cv(meta["cv"], "/cv", roi)

    steps: dict[str, int] = {}
    if (plan := meta["plan"]) is not None:
        _keys(plan, {"plan_id", "revision_id", "source_ref", "steps"}, "/plan")
        for key in ("plan_id", "revision_id", "source_ref"):
            _id(plan[key], f"/plan/{key}")
        if not isinstance(plan["steps"], list) or not plan["steps"]:
            raise _invalid("/plan/steps", "пустой план передаётся как plan = null")
        if len(plan["steps"]) > LIMITS["plan_steps_max"]:
            raise Problem(413, "payload_too_large", "слишком много шагов плана", "/plan/steps")
        order = []
        for n, step in enumerate(plan["steps"]):
            where = f"/plan/steps/{n}"
            _keys(step, STEP_KEYS, where)
            key = _id(step["step_key"], f"{where}/step_key")
            if key in steps:
                raise _invalid(f"{where}/step_key", "step_key повторяется")
            seq = step["sequence_no"]
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or any(seq == s for s, _, _ in order):
                raise _invalid(f"{where}/sequence_no", "sequence_no — положительное целое, без повторов")
            sid = step["stage_id"]
            work = WORK.get(sid) if isinstance(sid, int) and not isinstance(sid, bool) else None
            if work is None or work["stage_kind"] not in ("concrete", "no_class"):
                raise _invalid(
                    f"{where}/stage_id", f"{sid!r} — не работа справочника (сводный этап или неизвестный)", "unknown_stage"
                )
            if meta["object_type_code"] and meta["object_type_code"] not in work["object_type_codes"]:
                raise _invalid(
                    f"{where}/stage_id", f"«{work['name_ru']}» не относится к типу {meta['object_type_code']}", "unknown_stage"
                )
            start, end = (
                _time(step["planned_start_at"], f"{where}/planned_start_at"),
                _time(step["planned_end_at"], f"{where}/planned_end_at"),
            )
            if end <= start:
                raise _invalid(where, "конец плана должен быть строго позже начала")
            order.append((seq, start, where))
            steps[key] = sid
        order.sort(key=lambda item: item[0])
        for (_, before, _), (_, start, where) in zip(order, order[1:], strict=False):
            if start < before:
                raise _invalid(where, "начала работ убывают по sequence_no")
        identity = (meta["site_id"], scope["plan_stream_code"], plan["plan_id"], plan["revision_id"])
        content = json.dumps(plan["steps"], sort_keys=True)
        if plans.setdefault(identity, content) != content:
            raise Problem(409, "plan_identity_conflict", "та же версия плана с другим содержимым", "/plan/revision_id")

    history = _keys(
        meta["history"], {"snapshot_id", "as_of", "window_start", "complete", "observations", "progress_events"}, "/history"
    )
    _id(history["snapshot_id"], "/history/snapshot_id")
    as_of, window_start = _time(history["as_of"], "/history/as_of"), _time(history["window_start"], "/history/window_start")
    if as_of != observed:
        raise _invalid("/history/as_of", "as_of равен моменту frame.observed_at")
    if window_start > as_of:
        raise _invalid("/history/window_start", "window_start позже as_of")
    if not isinstance(history["complete"], bool):
        raise _invalid("/history/complete", "complete — true или false")
    observations = history["observations"]
    if not isinstance(observations, list) or len(observations) > LIMITS["history_observations_max"]:
        raise Problem(413, "payload_too_large", "наблюдений больше лимита", "/history/observations")
    frames_seen = {(frame["camera_id"], frame["image_id"])}
    for n, obs in enumerate(observations):
        where = f"/history/observations/{n}"
        _keys(obs, OBSERVATION_KEYS, where)
        for key in ("observation_id", "image_id", "camera_id", "source_ref"):
            _id(obs[key], f"{where}/{key}")
        at = _time(obs["observed_at"], f"{where}/observed_at")
        if not window_start <= at <= as_of:
            raise _invalid(f"{where}/observed_at", "наблюдение вне окна истории")
        if (obs["camera_id"], obs["image_id"]) in frames_seen:
            raise _invalid(where, "кадр повторяется (или это текущий кадр)")
        frames_seen.add((obs["camera_id"], obs["image_id"]))
        if not isinstance(obs["image_sha256"], str) or not SHA.match(obs["image_sha256"]):
            raise _invalid(f"{where}/image_sha256", "64 строчных hex-символа")
        _cv(obs["cv"], f"{where}/cv", roi)

    events = history["progress_events"]
    if not isinstance(events, list) or len(events) > LIMITS["progress_events_max"]:
        raise Problem(413, "payload_too_large", "событий больше лимита", "/history/progress_events")
    if events and plan is None:
        raise _invalid("/history/progress_events", "без плана событиям не на что ссылаться")
    moments: dict[tuple, str] = {}
    for n, ev in enumerate(events):
        where = f"/history/progress_events/{n}"
        _keys(ev, EVENT_KEYS, where)
        for key in ("event_id", "step_key", "plan_revision_id", "source_ref"):
            _id(ev[key], f"{where}/{key}")
        if steps.get(ev["step_key"]) != ev["stage_id"]:
            raise _invalid(where, "событие не ссылается на шаг текущего плана с тем же stage_id")
        if ev["state"] not in ("not_started", "in_progress", "completed"):
            raise _invalid(f"{where}/state", "state — not_started, in_progress или completed")
        effective, recorded = _time(ev["effective_at"], f"{where}/effective_at"), _time(ev["recorded_at"], f"{where}/recorded_at")
        started = _time(ev["actual_started_at"], f"{where}/actual_started_at", nullable=True)
        completed = _time(ev["actual_completed_at"], f"{where}/actual_completed_at", nullable=True)
        if effective > as_of or recorded > as_of or recorded < effective:
            raise _invalid(where, "effective_at ≤ recorded_at ≤ as_of")
        if (
            (started and started > effective)
            or (completed and completed > effective)
            or (started and completed and completed < started)
        ):
            raise _invalid(where, "фактические времена — не позже effective_at, окончание не раньше начала")
        if (completed and ev["state"] != "completed") or (ev["state"] == "not_started" and (started or completed)):
            raise _invalid(where, "фактические времена не соответствуют состоянию")
        key = (ev["step_key"], effective, recorded)
        if moments.setdefault(key, ev["state"]) != ev["state"]:
            raise _invalid(where, "разные состояния шага на один момент", "ambiguous_progress")


# ---------- ответ ----------
def _latest_events(meta: dict) -> dict[str, tuple[int, dict]]:
    latest: dict[str, tuple[int, dict]] = {}
    for n, ev in enumerate(meta["history"]["progress_events"]):
        key = (datetime.fromisoformat(ev["effective_at"]), datetime.fromisoformat(ev["recorded_at"]))
        known = latest.get(ev["step_key"])
        if known is None or key > (
            datetime.fromisoformat(known[1]["effective_at"]),
            datetime.fromisoformat(known[1]["recorded_at"]),
        ):
            latest[ev["step_key"]] = (n, ev)
    return latest


def _current_work(meta: dict, service: str) -> tuple[str, list[dict], list[dict], list[dict]]:
    """(status, группы, визуальные наблюдения, визуальные отношения)."""
    if meta["scope"]["plan_stream_code"] is None:
        return "scope_unknown", [], [], []
    if meta["plan"] is None:
        return "no_plan", [], [], []
    if all(WORK[step["stage_id"]]["stage_kind"] != "concrete" for step in meta["plan"]["steps"]):
        return "insufficient_evidence", [], [], []  # план только из работ без техники: по кадру назвать нечего
    cv = meta["cv"]
    usable = [(n, d) for n, d in enumerate(cv["detections"]) if (d["confidence"] or 0) >= 0.3] if cv["status"] == "ok" else []
    counts = Counter(d["class_code"] for _, d in usable)
    if not counts:
        return "insufficient_evidence", [], [], []
    scored = []
    for step in meta["plan"]["steps"]:
        roles, signals = ROLES.get(step["stage_id"], {}), SIGNALS.get(step["stage_id"], {})
        matches = {code: weight for code, weight in roles.items() if counts[code]}
        if sum(matches.values()) >= 2:  # роли решают, сигналы текущей активности — при равных ролях
            scored.append(((sum(matches.values()), sum(signals[c] for c in matches)), step, matches))
    if not scored:
        return "outside_plan", [], [], []
    scored.sort(key=lambda item: (-item[0][0], -item[0][1], item[1]["stage_id"]))  # при равных — по номеру вида работ
    if service == "deterministic":  # как у сервиса коллеги: все совместимые с общим CV участка, по убыванию
        top = [(step, matches) for _, step, matches in scored][:10]
    else:
        top = [(step, matches) for score, step, matches in scored if score == scored[0][0]][:10]
    matches = {code: weight for _, step_matches in top for code, weight in step_matches.items()}
    support = [(n, d) for n, d in usable if d["class_code"] in matches]
    area = [min(d["bbox"][0] for _, d in support), min(d["bbox"][1] for _, d in support),
            max(d["bbox"][2] for _, d in support), max(d["bbox"][3] for _, d in support)]  # fmt: skip
    names = ", ".join(CLASS_NAMES[c].lower() for c in matches)
    works = " или ".join(
        dict.fromkeys(f"«{WORK[step['stage_id']]['name_ru']}»" for step, _ in top[:3])
    )  # один вид работ — один раз
    observations, relations, evidence = [], [], []
    if service == "deterministic":
        for n, d in support[:24]:
            evidence.append({"source": "cv_detection", "ref": f"/cv/detections/{n}", "role": "supports",
                             "explanation": f"{CLASS_NAMES[d['class_code']]}: технологически совместим с кандидатами по матрице"})  # fmt: skip
        state = "not_evaluated"
        explanation = f"Совместимость техники на кадре ({names}) с планом: {works}. Параллельные операции не локализованы."
        area = None  # общий CV участка: где идёт какая работа, матрица не говорит
    else:
        firsts = {}
        for _, d in support:
            firsts.setdefault(d["class_code"], d)
        for k, (code, d) in enumerate(firsts.items()):
            observations.append({"id": f"vo{k + 1}", "description": f"{CLASS_NAMES[code]} в рабочей зоне",
                                 "certainty": "high" if (d["confidence"] or 0) >= 0.6 else "medium", "bbox": d["bbox"]})  # fmt: skip
            evidence.append({"source": "visual_observation", "ref": f"/visual_observations/{k}", "role": "supports",
                             "explanation": f"На снимке виден {CLASS_NAMES[code].lower()}"})  # fmt: skip
        ids = {code: f"vo{k + 1}" for k, code in enumerate(firsts)}
        if "Excavator" in ids and "DumpTruck" in ids:
            relations.append({"id": "vr1", "subject_id": ids["Excavator"], "predicate": "adjacent_to", "object_id": ids["DumpTruck"],
                              "certainty": "medium", "description": "Экскаватор рядом с самосвалом — похоже на погрузку грунта"})  # fmt: skip
            evidence.append({"source": "visual_relation", "ref": "/visual_relations/0", "role": "supports",
                             "explanation": "Экскаватор грузит самосвал"})  # fmt: skip
        state = "operation_indicated" if len(firsts) >= 2 else "presence_or_result_only"
        explanation = f"На снимке {names}: {'идёт работа' if len(firsts) >= 2 else 'техника есть, но работы не видно'} — {works}."
    candidates = []
    for step, step_matches in top:
        ranking = None
        if service == "deterministic":
            signals = {c: min(w, SIGNALS[step["stage_id"]][c]) for c, w in step_matches.items()}
            strong = [c for c, value in signals.items() if value >= 2]  # признак текущей работы, а не просто присутствия
            ranking = {
                "status": "supported_candidate" if len(strong) >= 2 else "single_signal" if strong else "context_only",
                "signal_score": min(
                    10.0, round(sum(value**3 / 4 for value in signals.values()), 2)
                ),  # как у коллеги: 1 → 0,25; 2 → 2
                "role_score": float(min(10, sum(step_matches.values()))),
                "matches": [
                    {
                        "class_code": c,
                        "role_weight": w,
                        "signal_weight": signals[c],
                        "evidence_group": CLASS_GROUPS.get(c, "equipment"),
                    }
                    for c, w in step_matches.items()
                ],
            }
        candidates.append({"step_key": step["step_key"], "stage_id": step["stage_id"], "ranking": ranking})
    group = {
        "group_id": "g1",
        "match_status": "specific" if len(candidates) == 1 else "ambiguous",
        "candidates": candidates,
        "visual_state": state,
        "area_bbox": None if area is None else [round(v, 6) for v in area],
        "evidence": evidence[:24],
        "explanation": explanation[:1000],
    }
    return "assessed", [group], observations, relations


def _transition(meta: dict) -> dict:
    result = {"status": "", "current_step_key": None, "next_step_key": None, "distinctive_codes": [], "evidence_first_at": None,
              "evidence_last_at": None, "supporting_observations": 0, "evidence_refs": []}  # fmt: skip
    plan = meta["plan"]
    if plan is None:
        return {**result, "status": "no_published_plan"}
    steps = sorted(plan["steps"], key=lambda s: s["sequence_no"])
    latest = _latest_events(meta)
    confirmed = [s for s in steps if s["step_key"] in latest and latest[s["step_key"]][1]["state"] != "not_started"]
    if not confirmed:
        return {**result, "status": "needs_progress_anchor"}
    current = confirmed[-1]
    result["current_step_key"] = current["step_key"]
    following = [s for s in steps if s["sequence_no"] > current["sequence_no"]]
    if not following:
        return {**result, "status": "no_next_stage"}
    nxt = following[0]
    result["next_step_key"] = nxt["step_key"]
    distinctive = sorted(set(ROLES.get(nxt["stage_id"], {})) - set(ROLES.get(current["stage_id"], {})))
    result["distinctive_codes"] = distinctive
    if not distinctive:
        return {**result, "status": "not_distinguishable_by_equipment"}
    history = meta["history"]
    anchor = datetime.fromisoformat(latest[current["step_key"]][1]["effective_at"])
    as_of, start = datetime.fromisoformat(history["as_of"]), datetime.fromisoformat(history["window_start"])
    if not history["complete"] or (as_of - start < timedelta(days=7) and start > anchor):
        return {**result, "status": "history_incomplete"}
    frames = [
        (datetime.fromisoformat(o["observed_at"]), o["cv"], f"/history/observations/{n}/cv")
        for n, o in enumerate(history["observations"])
    ]
    frames.append((datetime.fromisoformat(meta["frame"]["observed_at"]), meta["cv"], "/cv"))
    if all(cv["status"] != "ok" for _, cv, _ in frames):
        return {**result, "status": "cv_unavailable"}
    hits = sorted(
        (at, f"{ref}/detections/{n}")
        for at, cv, ref in frames
        for n, d in enumerate(cv["detections"])
        if d["class_code"] in distinctive and at >= anchor
    )
    if not hits:
        return {**result, "status": "no_signal"}
    points, refs = [], []
    for at, ref in hits:
        if not points or at - points[-1] >= timedelta(minutes=15):
            points.append(at)
            refs.append(ref)
    status = "possible_start" if len(points) >= 2 else "single_or_short_signal"
    return {**result, "status": status, "evidence_first_at": hits[0][0].isoformat(), "evidence_last_at": hits[-1][0].isoformat(),
            "supporting_observations": len(points), "evidence_refs": refs[:24]}  # fmt: skip


def _schedule(meta: dict) -> dict:
    plan = meta["plan"]
    if plan is None:
        return {"status": "insufficient_evidence", "items": []}
    as_of = datetime.fromisoformat(meta["history"]["as_of"])
    latest = _latest_events(meta)
    items = []
    for step in sorted(plan["steps"], key=lambda s: s["sequence_no"]):
        end = datetime.fromisoformat(step["planned_end_at"])
        n, ev = latest.get(step["step_key"], (None, None))
        refs = [f"/history/progress_events/{n}"] if ev else []
        effective = datetime.fromisoformat(ev["effective_at"]) if ev else None
        done = datetime.fromisoformat(ev["actual_completed_at"]) if ev and ev["actual_completed_at"] else None
        if done and done > end:
            item = ("possible_delay", "actual_completion_after_deadline", (done - end).total_seconds(), ev["effective_at"])
        elif ev and ev["state"] != "completed" and effective > end:
            item = ("possible_delay", "open_state_after_deadline", (effective - end).total_seconds(), ev["effective_at"])
        elif done:
            item = ("no_delay_indicated", "completed_by_deadline", 0, ev["effective_at"])
        elif end > as_of:
            item = ("no_delay_indicated", "deadline_not_reached", 0, meta["history"]["as_of"])
            refs = []
        elif ev and ev["state"] == "completed":
            item = ("insufficient_evidence", "actual_completion_time_unknown", None, ev["effective_at"])
        else:
            item = ("insufficient_evidence", "no_sourced_progress", None, meta["history"]["as_of"])
        status, reason, overdue, evidence_at = item
        items.append({"step_key": step["step_key"], "stage_id": step["stage_id"], "status": status, "reason_code": reason,
                      "overdue_seconds": overdue if overdue is None else int(overdue), "evidence_at": evidence_at, "evidence_refs": refs})  # fmt: skip
    statuses = {i["status"] for i in items}
    overall = next(
        (s for s in ("possible_delay", "insufficient_evidence", "no_delay_indicated") if s in statuses), "insufficient_evidence"
    )
    return {"status": overall, "items": items}


def answer(service: str, meta: dict, input_sha256: str) -> dict:
    status, groups, observations, relations = _current_work(meta, service)
    plan, frame = meta["plan"], meta["frame"]
    deterministic = service == "deterministic"
    limitations = ["Имитация сервиса: матрица «класс техники → вид работ» упрощена, ответ не от настоящей аналитики."]
    if not deterministic:
        limitations.append("Имитация: изображение не анализируется, визуальные наблюдения построены по рамкам CV.")
    if meta["cv"]["status"] != "ok":
        limitations.append(f"CV кадра: {meta['cv']['status']} — техника по кадру не учитывалась.")
    return {
        "schema_version": "frame-analysis-result-v1",
        "request_id": meta["request_id"],
        "analysis_id": f"an-{service}-{uuid.uuid4().hex[:12]}",
        "service": service,
        "context": {
            "site_id": meta["site_id"],
            "camera_id": frame["camera_id"],
            "image_id": frame["image_id"],
            "observed_at": frame["observed_at"],
            "image_sha256": frame["image_sha256"],
            "plan_id": plan["plan_id"] if plan else None,
            "plan_revision_id": plan["revision_id"] if plan else None,
            "plan_stream_code": meta["scope"]["plan_stream_code"],
            "history_snapshot_id": meta["history"]["snapshot_id"],
            "catalog_version": meta["catalog_version"],
            "input_sha256": input_sha256,
        },
        "versions": {
            "service_version": SERVICE_VERSION,
            "matrix_version": MATRIX_VERSION if deterministic else None,
            "profiles_version": None if deterministic else PROFILES_VERSION,
            "vision_model": None if deterministic else "mock-vlm",
            "llm_model": None if deterministic else "mock-llm",
            "schedule_rules_version": "schedule-rules-v1" if deterministic else None,
        },
        "current_work": {"status": status, "work_groups": groups},
        "visual_observations": observations,
        "visual_relations": relations,
        "transition": _transition(meta)
        if deterministic
        else {
            "status": "not_evaluated",
            "current_step_key": None,
            "next_step_key": None,
            "distinctive_codes": [],
            "evidence_first_at": None,
            "evidence_last_at": None,
            "supporting_observations": 0,
            "evidence_refs": [],
        },
        "schedule": _schedule(meta) if deterministic else {"status": "not_evaluated", "items": []},
        "limitations": limitations[:20],
    }


# ---------- HTTP ----------
def _error(service: str, problem: Problem, request_id: str | None = None, headers: dict | None = None) -> JSONResponse:
    body = {
        "schema_version": "frame-analysis-error-v1",
        "request_id": request_id,
        "service": service,
        "code": problem.code,
        "message": problem.message,
        "details": [{"path": problem.path, "code": problem.code, "message": problem.message}],
        "retryable": problem.retryable,
        "execution_state": "not_started" if problem.status < 500 else "failed",
    }
    return JSONResponse(body, status_code=problem.status, headers=headers)


def make_service(service: str) -> FastAPI:
    api = FastAPI(title=f"Имитация сервиса аналитики {service}")
    journal: dict[tuple[str, str], tuple[str, dict]] = {}  # (site_id, request_id) → (отпечаток, ответ)
    running: set[tuple[str, str]] = set()
    plans: dict[tuple, str] = {}
    observed: dict[tuple[str, str, str], tuple[str, str]] = {}  # (site, camera, image) → (sha256, время)

    def authorized(request: Request) -> bool:
        return not TOKEN or hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {TOKEN}".encode())

    @api.get("/health/live")
    async def live() -> dict:
        return {"status": "alive"}

    @api.get("/health/ready")
    async def ready() -> dict:
        return {"status": "ready", "service": service, "service_version": SERVICE_VERSION}

    @api.get("/v1/capabilities")
    async def capabilities(request: Request):  # noqa: ANN202
        if not authorized(request):
            return _error(service, Problem(401, "unauthorized", "нужен Authorization: Bearer <токен>"))
        return {"schema_version": "frame-analysis-capabilities-v1", "service": service, "service_version": SERVICE_VERSION,
                "input_version": "frame-analysis-input-v1", "result_version": "frame-analysis-result-v1",
                "catalog_version": CATALOG_VERSION, "limits": LIMITS}  # fmt: skip

    @api.get("/v1/catalog")
    async def catalog(request: Request):  # noqa: ANN202
        if not authorized(request):
            return _error(service, Problem(401, "unauthorized", "нужен Authorization: Bearer <токен>"))
        return CATALOG

    @api.get("/v1/analyses/by-request/{request_id}")
    async def by_request(request_id: str, site_id: str, request: Request):  # noqa: ANN202
        if not authorized(request):
            return _error(service, Problem(401, "unauthorized", "нужен Authorization: Bearer <токен>"), request_id)
        if (site_id, request_id) in running:
            return _error(service, Problem(429, "busy", "анализ ещё идёт", retryable=True), request_id, {"Retry-After": "1"})
        if (site_id, request_id) in journal:
            return journal[(site_id, request_id)][1]
        return _error(service, Problem(404, "analysis_not_found", "такого запроса в журнале нет"), request_id)

    @api.post("/v1/analyze/frame")
    async def analyze(request: Request):  # noqa: ANN202
        if not authorized(request):
            return _error(service, Problem(401, "unauthorized", "нужен Authorization: Bearer <токен>"))
        if "application/json" not in request.headers.get("accept", ""):
            return _error(service, Problem(400, "invalid_request", "нужен заголовок Accept: application/json"))
        form = await request.form()
        parts = form.multi_items()
        names = [name for name, _ in parts]
        if sorted(names) != ["image", "metadata"]:
            return _error(service, Problem(400, "invalid_request", f"нужны ровно две части: metadata и image, а пришли {names}"))
        meta_part, image_part = form["metadata"], form["image"]
        if not isinstance(meta_part, UploadFile) or not meta_part.filename:
            return _error(
                service, Problem(400, "invalid_request", "metadata — файловая часть с именем файла, например metadata.json")
            )
        if not (meta_part.content_type or "").startswith("application/json"):
            return _error(service, Problem(415, "unsupported_media_type", "тип части metadata — application/json"))
        if not isinstance(image_part, UploadFile) or image_part.content_type not in MEDIA:
            return _error(service, Problem(415, "unsupported_media_type", "кадр — image/jpeg, image/png или image/webp"))
        raw, image = await meta_part.read(), await image_part.read()
        if len(raw) > LIMITS["metadata_max_bytes"] or len(image) > LIMITS["image_max_bytes"]:
            return _error(service, Problem(413, "payload_too_large", "metadata или кадр больше лимита"))
        try:
            meta = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return _error(service, Problem(400, "invalid_request", "metadata — не JSON в UTF-8"))
        request_id = meta.get("request_id") if isinstance(meta, dict) else None
        if request.headers.get("idempotency-key") != request_id:
            return _error(service, Problem(400, "invalid_request", "Idempotency-Key должен совпадать с request_id"), request_id)
        fingerprint = hashlib.sha256(raw + b"\x00" + image).hexdigest()
        key = (str(meta.get("site_id")), str(request_id))
        if key in running:
            return _error(
                service, Problem(429, "busy", "этот запрос уже выполняется", retryable=True), request_id, {"Retry-After": "1"}
            )
        if key in journal:
            stored_fingerprint, stored = journal[key]
            if stored_fingerprint != fingerprint:
                return _error(service, Problem(409, "idempotency_conflict", "тот же request_id с другим содержимым"), request_id)
            return stored  # повтор того же запроса — прежний ответ с прежним analysis_id
        try:
            validate(meta, image, image_part.content_type, plans)
            for obs in meta["history"]["observations"]:
                identity = (meta["site_id"], obs["camera_id"], obs["image_id"])
                if observed.setdefault(identity, (obs["image_sha256"], obs["observed_at"])) != (
                    obs["image_sha256"],
                    obs["observed_at"],
                ):
                    raise Problem(
                        409, "observation_identity_conflict", "тот же кадр с другим sha256 или временем", "/history/observations"
                    )
            frame = meta["frame"]
            observed.setdefault(
                (meta["site_id"], frame["camera_id"], frame["image_id"]), (frame["image_sha256"], frame["observed_at"])
            )
        except Problem as problem:
            return _error(service, problem, request_id)
        if fault := FAULTS.get(service):
            status = {"model_failure": 502, "model_invalid_response": 502, "not_ready": 503, "analysis_timeout": 504}.get(
                fault, 500
            )
            return _error(service, Problem(status, fault, f"имитация отказа: {fault}"), request_id)
        running.add(key)
        try:
            await asyncio.sleep(DELAYS_S[service])
            result = answer(service, meta, fingerprint)
        finally:
            running.discard(key)
        journal[key] = (fingerprint, result)
        return result

    return api


app = FastAPI(title="Имитация сервисов аналитики (frame-analysis-v1)")
app.mount("/deterministic", make_service("deterministic"))
app.mount("/vlm_llm", make_service("vlm_llm"))


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "services": ["deterministic", "vlm_llm"], "catalog_version": CATALOG_VERSION, "faults": FAULTS}
