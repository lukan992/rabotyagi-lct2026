"""Проверка ответа сервиса аналитики до сохранения (разделы 8 и 12 контракта).

Ответ должен относиться именно к этому запросу: те же кадр, версия плана, участок, снимок истории, справочник и
отпечаток входа. Несовпадение — технический конфликт, а не согласие. Кандидаты групп — пункты отправленного плана
с тем же stage_id. Полностью ответ проверяет схема контракта; здесь — то, без чего его нельзя показывать людям.
"""

import math
from datetime import datetime
from typing import Any

RESULT_SCHEMAS = {
    "frame-analysis-input-v1": "frame-analysis-result-v1",
    "frame-analysis-input-v2": "frame-analysis-result-v2",
}
OUTCOMES = ("assessed", "insufficient_evidence", "outside_plan", "no_plan", "scope_unknown")
MAX_GROUPS = 8

RESOURCE_STATUSES = {"compared", "demonstration", "insufficient_evidence", "not_evaluated"}
RESOURCE_ITEM_STATUSES = {"visible_below_plan", "visible_equal_plan", "visible_above_plan", "unknown"}
RESOURCE_REASONS = {
    "no_resource_plan", "no_resource_target", "scope_unknown", "resource_scope_unknown", "plan_class_unmapped",
    "duplicate_planned_class", "planned_quantity_unknown", "observation_unavailable", "partial_coverage",
    "unknown_coverage", "duplicate_observed_class", "observation_class_missing", "observation_class_unmapped",
    "non_production_source", "timestamp_unverified", "observation_requires_validation", "demonstration_mode",
    "no_independent_measurements", "no_planned_equipment", "vlm_resources_not_evaluated",
}
ASSESSMENT_FIELDS = {
    "status", "reason_codes", "stage_code", "selection_basis", "basis", "coverage", "equipment_items",
    "planned_volume", "planned_work_shifts", "planned_productivity", "actual_volume", "actual_productivity",
    "evidence_refs", "limitations",
}
EQUIPMENT_FIELDS = {
    "item_id", "class_code", "planned_quantity", "visible_count", "visible_count_delta", "status", "reason_codes",
    "evidence_refs",
}


def _id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 100 and bool(value.strip())


def _quantity(value: object, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= 1_000_000


def _pointer_exists(document: object, pointer: object) -> bool:
    """Resolve only RFC 6901 pointers into the exact immutable request metadata."""
    if not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 1000:
        return False
    current = document
    for raw in pointer[1:].split("/"):
        if "~" in raw and any(raw[index:index + 2] not in {"~0", "~1"} for index in range(len(raw)) if raw[index] == "~"):
            return False
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                return False
            index = int(token)
            if index >= len(current):
                return False
            current = current[index]
        else:
            return False
    return True


def _refs(value: object, metadata: dict, *, maximum: int) -> bool:
    return isinstance(value, list) and len(value) <= maximum and all(_pointer_exists(metadata, pointer) for pointer in value)


def _reason_codes(value: object, *, maximum: int) -> bool:
    return isinstance(value, list) and len(value) <= maximum and all(isinstance(code, str) and code in RESOURCE_REASONS for code in value)


def _measure(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"value", "unit"}
        and isinstance(value["value"], int | float)
        and not isinstance(value["value"], bool)
        and math.isfinite(value["value"])
        and 0 <= value["value"] <= 10**15
        and isinstance(value["unit"], str)
        and 0 < len(value["unit"]) <= 100
        and bool(value["unit"].strip())
    )


def _check_resource_assessment(payload: dict, metadata: dict, service: str) -> str | None:
    assessment = payload.get("resource_assessment")
    if not isinstance(assessment, dict):
        return "в v2-ответе нет resource_assessment"
    if set(assessment) != ASSESSMENT_FIELDS:
        return "resource_assessment не соответствует набору полей контракта"
    if not isinstance(assessment["status"], str) or assessment["status"] not in RESOURCE_STATUSES:
        return "resource_assessment.status недопустим"
    if not _reason_codes(assessment["reason_codes"], maximum=30):
        return "resource_assessment.reason_codes недопустим"
    if assessment["basis"] != metadata["equipment_observation"]["basis"]:
        return "resource_assessment.basis не совпадает с запросом"
    if assessment["coverage"] != metadata["equipment_observation"]["coverage"]:
        return "resource_assessment.coverage не совпадает с запросом"
    if not isinstance(assessment["coverage"], str) or assessment["coverage"] not in {"full_scope", "partial_scope", "unknown"}:
        return "resource_assessment.coverage недопустим"
    if not _refs(assessment["evidence_refs"], metadata, maximum=10):
        return "resource_assessment.evidence_refs должен ссылаться на metadata того же кадра"
    limitations = assessment["limitations"]
    if not isinstance(limitations, list) or not 1 <= len(limitations) <= 10 or any(
        not isinstance(item, str) or not 0 < len(item) <= 1000 or not item.strip() for item in limitations
    ):
        return "resource_assessment.limitations недопустим"
    if not _measure(assessment["planned_volume"]) or not _measure(assessment["planned_productivity"]):
        return "resource_assessment содержит некорректную меру"
    if assessment["actual_volume"] is not None or assessment["actual_productivity"] is not None:
        return "фактический объём и производительность не подтверждены независимым измерением"
    if assessment["planned_work_shifts"] is not None and (
        isinstance(assessment["planned_work_shifts"], bool)
        or not isinstance(assessment["planned_work_shifts"], int | float)
        or not math.isfinite(assessment["planned_work_shifts"])
        or not 0 < assessment["planned_work_shifts"] <= 1_000_000
    ):
        return "resource_assessment.planned_work_shifts недопустим"

    resource_plan = metadata.get("resource_plan")
    stages = resource_plan.get("stages") if isinstance(resource_plan, dict) else []
    stages_by_code = {stage.get("stage_code"): stage for stage in stages if isinstance(stage, dict)}
    stage_code = assessment["stage_code"]
    if stage_code is not None and not _id(stage_code):
        return "resource_assessment.stage_code недопустим"
    stage = stages_by_code.get(stage_code) if stage_code is not None else None
    if stage_code is None:
        if assessment["selection_basis"] is not None or assessment["equipment_items"]:
            return "оценка без ресурсного этапа не может содержать выбор или строки техники"
        if any(assessment[field] is not None for field in ("planned_volume", "planned_work_shifts", "planned_productivity")):
            return "оценка без ресурсного этапа не может содержать плановые показатели"
    else:
        if stage is None:
            return "resource_assessment.stage_code не относится к отправленному resource_plan"
        if assessment["selection_basis"] not in {"planned_at_frame_time", "confirmed_current", "explicit"}:
            return "resource_assessment.selection_basis недопустим"
        target = metadata.get("resource_target")
        if (
            not isinstance(target, dict)
            or target.get("stage_code") != stage_code
            or target.get("selection_basis") != assessment["selection_basis"]
        ):
            return "resource_assessment.stage_code и selection_basis должны совпадать с ресурсной целью"
        for field in ("planned_volume", "planned_work_shifts", "planned_productivity"):
            if assessment[field] != stage.get(field):
                return f"resource_assessment.{field} не совпадает с выбранным ресурсным этапом"

    mode = metadata["analysis_mode"]
    if not isinstance(mode, str) or mode not in {"demonstration", "operational"}:
        return "analysis_mode запроса недопустим"
    items = assessment["equipment_items"]
    if not isinstance(items, list) or len(items) > 200:
        return "resource_assessment.equipment_items недопустим"
    planned_items = (
        {item.get("item_id"): item for item in stage.get("equipment", []) if isinstance(item, dict)}
        if stage and isinstance(stage.get("equipment"), list)
        else {}
    )
    seen: set[str] = set()
    compared = False
    observation = metadata["equipment_observation"]
    source = metadata["source_context"]
    trusted_operational = (
        source.get("data_type") == "production"
        and source.get("timestamp_quality") == "verified"
        and observation.get("requires_validation") is False
    )
    for item in items:
        if not isinstance(item, dict) or set(item) != EQUIPMENT_FIELDS or not _id(item.get("item_id")):
            return "строка resource_assessment.equipment_items недопустима"
        item_id = item["item_id"]
        if item_id in seen or item_id not in planned_items:
            return "строка техники не относится к выбранному этапу или повторена"
        seen.add(item_id)
        planned = planned_items[item_id]
        if item["class_code"] != planned.get("class_code") or item["planned_quantity"] != planned.get("planned_quantity"):
            return "строка техники не совпадает с отправленным ресурсным планом"
        if item["class_code"] is not None and not _id(item["class_code"]):
            return "class_code строки техники недопустим"
        if item["planned_quantity"] is not None and not _quantity(item["planned_quantity"]):
            return "planned_quantity строки техники недопустим"
        if item["visible_count"] is not None and not _quantity(item["visible_count"]):
            return "visible_count строки техники недопустим"
        if item["visible_count_delta"] is not None and not _quantity(item["visible_count_delta"], minimum=-1_000_000):
            return "visible_count_delta строки техники недопустим"
        if not isinstance(item["status"], str) or item["status"] not in RESOURCE_ITEM_STATUSES or not _reason_codes(
            item["reason_codes"], maximum=20
        ):
            return "статус или причины строки техники недопустимы"
        if not _refs(item["evidence_refs"], metadata, maximum=205):
            return "evidence_refs строки техники должен ссылаться на metadata того же кадра"
        delta = item["visible_count_delta"]
        if delta is None:
            if item["status"] != "unknown":
                return "строка без delta должна иметь status = unknown"
            continue
        if item["planned_quantity"] is None or item["visible_count"] is None or delta != item["visible_count"] - item["planned_quantity"]:
            return "visible_count_delta не соответствует плановому и видимому количеству"
        expected_status = "visible_below_plan" if delta < 0 else "visible_equal_plan" if delta == 0 else "visible_above_plan"
        if item["status"] != expected_status:
            return "status строки техники не соответствует visible_count_delta"
        if assessment["coverage"] != "full_scope":
            return "числовое сравнение недопустимо при неполном охвате"
        if not isinstance(resource_plan, dict) or resource_plan.get("plan_stream_code") != metadata["scope"]["plan_stream_code"]:
            return "числовое сравнение недопустимо без привязки resource_plan к plan_stream"
        if mode == "operational" and not trusted_operational:
            return "операционное числовое сравнение недопустимо при непроверенном происхождении"
        if planned.get("mapping_status") != "mapped" or item["class_code"] is None:
            return "числовое сравнение недопустимо для unmapped техники"
        compared = True

    if service == "deterministic" and mode == "demonstration":
        if assessment["status"] != "demonstration" or "demonstration_mode" not in assessment["reason_codes"]:
            return "demonstration должен быть явно отмечен в resource_assessment"
    if service == "deterministic" and mode == "operational":
        if assessment["status"] == "demonstration":
            return "operational-ответ не может иметь demonstration assessment"
        if assessment["status"] == "compared" and not compared:
            return "compared требует хотя бы одной допустимой числовой строки"
        if compared and assessment["status"] != "compared":
            return "числовая строка требует resource_assessment.status = compared"
    if service == "vlm_llm":
        if assessment["status"] != "not_evaluated" or items:
            return "VLM должен вернуть resource_assessment.status = not_evaluated без числового сравнения"
        versions = payload.get("versions")
        if not isinstance(versions, dict) or versions.get("resource_rules_version") is not None:
            return "VLM не должен указывать версию правил ресурсов"
    elif service == "deterministic":
        if assessment["status"] == "not_evaluated":
            return "deterministic не должен возвращать not_evaluated"
        versions = payload.get("versions")
        if not isinstance(versions, dict) or versions.get("resource_rules_version") != "resource-rules-v1":
            return "deterministic должен указать resource-rules-v1"
    else:
        return f"неизвестный сервис v2 {service!r}"
    return None


def _same_moment(a: Any, b: str) -> bool:
    try:
        return datetime.fromisoformat(str(a)) == datetime.fromisoformat(b)
    except ValueError:
        return False


def check_result(payload: dict, *, service: str, metadata: dict, input_sha256: str) -> str | None:
    """Что не так с ответом; None — ответ относится к запросу и его можно показывать."""
    input_schema = metadata.get("schema_version")
    expected_schema = RESULT_SCHEMAS.get(input_schema)
    if expected_schema is None:
        return f"неподдерживаемая schema_version запроса {input_schema!r}"
    if input_schema == "frame-analysis-input-v2" and service not in {"deterministic", "vlm_llm"}:
        return f"неизвестный сервис v2 {service!r}"
    if payload.get("schema_version") != expected_schema:
        return f"schema_version ответа {payload.get('schema_version')!r}, а нужен {expected_schema}"
    if payload.get("request_id") != metadata["request_id"]:
        return f"request_id ответа {payload.get('request_id')!r} — не от этого запроса"
    if payload.get("service") != service:
        return f"ответ подписан сервисом {payload.get('service')!r}, а спрашивали {service}"
    context = payload.get("context")
    if not isinstance(context, dict):
        return "в ответе нет context"
    frame, plan = metadata["frame"], metadata["plan"]
    expected = {
        "site_id": metadata["site_id"],
        "camera_id": frame["camera_id"],
        "image_id": frame["image_id"],
        "image_sha256": frame["image_sha256"],
        "plan_id": plan["plan_id"] if plan else None,
        "plan_revision_id": plan["revision_id"] if plan else None,
        "plan_stream_code": metadata["scope"]["plan_stream_code"],
        "history_snapshot_id": metadata["history"]["snapshot_id"],
        "catalog_version": metadata["catalog_version"],
        "input_sha256": input_sha256,
    }
    if input_schema == "frame-analysis-input-v2":
        source_context = metadata.get("source_context")
        if not isinstance(source_context, dict):
            return "в v2-запросе нет source_context"
        if not isinstance(metadata.get("equipment_observation"), dict):
            return "в v2-запросе нет equipment_observation"
        if metadata.get("analysis_mode") not in {"demonstration", "operational"}:
            return "в v2-запросе недопустимый analysis_mode"
        if payload.get("analysis_mode") != metadata.get("analysis_mode"):
            return "analysis_mode ответа не совпадает с запросом"
        if payload.get("source_context") != source_context:
            return "source_context ответа не совпадает с запросом"
        if problem := _check_resource_assessment(payload, metadata, service):
            return problem
    for key, value in expected.items():
        if context.get(key) != value:
            return f"context.{key} ответа {context.get(key)!r} не совпадает с запросом ({value!r}) — ответ не к этому кадру"
    if not _same_moment(context.get("observed_at"), frame["observed_at"]):
        return f"context.observed_at ответа {context.get('observed_at')!r} не совпадает со временем кадра"

    current = payload.get("current_work")
    if not isinstance(current, dict) or current.get("status") not in OUTCOMES:
        return f"current_work.status должен быть одним из {', '.join(OUTCOMES)}"
    groups = current.get("work_groups")
    if not isinstance(groups, list) or len(groups) > MAX_GROUPS:
        return f"current_work.work_groups — список не длиннее {MAX_GROUPS}"
    if (current["status"] == "assessed") != bool(groups):
        return "группы работ должны быть только при status = assessed, и тогда хотя бы одна"
    steps = {s["step_key"]: s["stage_id"] for s in plan["steps"]} if plan else {}
    for n, group in enumerate(groups):
        candidates = group.get("candidates") if isinstance(group, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return f"в группе {n} нет кандидатов"
        for c in candidates:
            if not isinstance(c, dict) or c.get("step_key") not in steps or steps[c["step_key"]] != c.get("stage_id"):
                return f"кандидат {c!r} группы {n} — не пункт отправленного плана"
    for block in ("transition", "schedule"):
        if not isinstance(payload.get(block), dict) or not isinstance(payload[block].get("status"), str):
            return f"в ответе нет {block}.status"
    return None
