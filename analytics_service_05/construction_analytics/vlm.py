"""Frame-contract adapter around the shared real VLM -> LLM pipeline."""
from __future__ import annotations

from .contracts import result_envelope, visual_step_keys
from .decision import describe_group
from .deterministic import latest_progress
from .pipeline import execute


def analyze(case, image, references, gateway, analysis_id, input_sha, save, *, equipment_activity=None):
    result = result_envelope(case, "vlm_llm", analysis_id, input_sha, references)
    result["versions"].update(vision_model=gateway.model, llm_model=gateway.model)
    if not case["plan"] or case["scope"]["plan_stream_code"] is None:
        result["limitations"].append("Планозависимый модельный вызов пропущен: план или привязка участка отсутствуют.")
        return result
    selectable = visual_step_keys(case, references)
    if not selectable:
        result["limitations"].append("План содержит только no_class: визуальный выбор невозможен, модельный вызов пропущен; требуется внешнее подтверждение хода работ.")
        return result
    latest = latest_progress(case)
    codes = sorted({d["class_code"] for d in case["cv"]["detections"]})
    steps = []
    for step in sorted(case["plan"]["steps"], key=lambda s: s["sequence_no"]):
        assertion = latest.get(step["step_key"])
        event = assertion[1] if assertion else None
        steps.append({**step, "stage_kind": references.stages[step["stage_id"]]["stage_kind"],
                      "progress_state": event["state"] if event else "unknown",
                      "progress_source_ref": event["source_ref"] if event else None,
                      "progress_effective_at": event["effective_at"] if event else None})
    model_case = {"image_id": case["frame"]["image_id"], "observed_at": case["frame"]["observed_at"],
                  "site_ref": case["site_id"], "plan_stream_code": case["scope"]["plan_stream_code"],
                  "plan_revision_ref": case["plan"]["revision_id"], "plan_steps": steps,
                  "equipment": [{"class_code": code} for code in codes], "frame_contract": True,
                  "cv_detections": case["cv"]["detections"], "progress_events": case["history"]["progress_events"],
                  "roi_bbox": case["scope"]["roi_bbox"], "selectable_step_keys": selectable}
    if case["schema_version"] == "frame-analysis-input-v2" and case["resource_plan"] is not None:
        model_case.update(resource_plan=case["resource_plan"], source_context=case["source_context"],
                          analysis_mode=case["analysis_mode"])
    if equipment_activity is not None:
        model_case["equipment_activity"] = {
            "rules_version": equipment_activity["rules_version"],
            "items": [{key: item[key] for key in ("detection_id", "class_code", "status",
                       "stationary_observation_span_seconds", "reason_codes")}
                      for item in equipment_activity["items"]],
            "limitations": equipment_activity["limitations"],
        }
    context = references.context([s["stage_id"] for s in steps], set(codes))
    output = execute(model_case, context, image, case["frame"]["media_type"], case["frame"]["image_sha256"], gateway, save)
    visual, decision = output["vision"], output["decision"]
    result["visual_observations"] = [{k: o[k] for k in ("id", "description", "certainty", "bbox")} for o in visual["observations"]]
    result["visual_relations"] = [{k: r[k] for k in ("id", "subject_id", "predicate", "object_id", "certainty", "description")} for r in visual["relations"]]
    refs = {"vision_observation": {o["id"]: f"/visual_observations/{i}" for i, o in enumerate(visual["observations"])},
            "vision_relation": {r["id"]: f"/visual_relations/{i}" for i, r in enumerate(visual["relations"])},
            "cv_detection": {d["detection_id"]: f"/cv/detections/{i}" for i, d in enumerate(case["cv"]["detections"])},
            "progress_event": {e["event_id"]: f"/history/progress_events/{i}" for i, e in enumerate(case["history"]["progress_events"])}}
    groups = []
    step_map = {s["step_key"]: s for s in steps}
    for index, group in enumerate(decision["work_groups"]):
        enriched = describe_group(group, model_case, context, visual)
        evidence = [{**e, "source": {"vision_observation": "visual_observation", "vision_relation": "visual_relation"}.get(e["source"], e["source"]),
                     "ref": refs[e["source"]][e["ref"]]} for e in group["evidence"]]
        groups.append({"group_id": "group-" + str(index + 1), "match_status": group["match_status"],
                       "visual_state": group["visual_state"], "area_bbox": enriched["area_bbox"],
                       "candidates": [{"step_key": k, "stage_id": step_map[k]["stage_id"], "ranking": None} for k in group["step_keys"]],
                       "evidence": evidence, "explanation": group["explanation"]})
    result["current_work"] = {"status": decision["status"], "work_groups": groups}
    limitations = result["limitations"] + decision["limitations"] + visual["limitations"]
    if case["cv"]["status"] != "ok":
        limitations.insert(0, "Внешний CV недоступен; визуальная гипотеза имеет неполный контекст техники.")
    result["limitations"] = list(dict.fromkeys(limitations))[:20]
    return result
