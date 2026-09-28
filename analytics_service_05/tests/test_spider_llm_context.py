import hashlib
import io
import json
import unittest

from PIL import Image

from construction_analytics.contracts import structural, validate_request
from construction_analytics.vlm import analyze


_image = io.BytesIO()
Image.new("RGB", (1, 1)).save(_image, "PNG")
PNG = _image.getvalue()


class References:
    catalog_version = "catalog-cf435bba295909ab953facca"
    equipment_codes = {"Excavator", "DumpTruck"}
    matrix = {"rule_version": "construction-class-matrix-v2.0"}
    profiles_version = "visual-profiles-708cb01523c740f39b8789aa"
    stages = {47: {"stage_kind": "concrete"}}

    def context(self, stage_ids, equipment_codes):
        self.asserted_stage_ids = stage_ids
        self.asserted_equipment_codes = equipment_codes
        return {
            "role": "reference",
            "guardrails": [],
            "selection_rules": [],
            "candidates": [{
                "stage_id": 47,
                "name_ru": "Local concrete work",
                "stage_path": "Local concrete work",
                "stage_kind": "concrete",
            }],
        }


class Gateway:
    model = "controlled-gateway"

    def __init__(self):
        self.image_prompts = []
        self.text_calls = []

    def image(self, image, mime, image_id, image_sha, prompt):
        self.image_prompts.append(prompt)
        return {
            "image_id": image_id,
            "image_sha256": image_sha,
            "model": self.model,
            "strict_json": True,
            "schema_valid": True,
            "payload": {
                "schema_version": "construction-context-v1",
                "image_id": image_id,
                "status": "ok",
                "scene_summary": "Видимая зона работ.",
                "image_quality": {"view": "outdoor", "detail": "adequate", "occlusion": "low", "issues": []},
                "observations": [{
                    "id": "vision-1", "category": "terrain", "label": "excavation",
                    "description": "Видна выемка грунта.", "state": "unknown", "certainty": "high",
                    "bbox": [0.1, 0.1, 0.9, 0.9], "visible_count": None,
                }],
                "relations": [],
                "checks": [{"target": target, "result": "not_observed", "evidence_ids": [], "reason": "Нет данных."}
                           for target in ("piles", "people", "excavation", "rebar", "formwork", "structural_frame",
                                          "stockpiled_materials", "floor_work_area")],
                "activity_hypotheses": [],
                "limitations": [],
            },
        }

    def text(self, prompt, schema, system):
        self.text_calls.append((prompt, schema, system))
        return {
            "strict_json": True,
            "payload": {
                "schema_version": "frame-work-groups-v1",
                "image_id": "frame-1",
                "status": "assessed",
                "work_groups": [{
                    "step_keys": ["local-step-1"], "match_status": "specific",
                    "visual_state": "presence_or_result_only", "area_observation_ids": ["vision-1"],
                    "evidence": [{"source": "vision_observation", "ref": "vision-1", "role": "supports",
                                  "explanation": "Визуальная зона подтверждает привязку."}],
                    "explanation": "Признаки совместимы с локальной работой.",
                }],
                "summary": "Есть визуальные признаки в зоне локального плана.",
                "limitations": [],
            },
        }


class SpiderPromptTests(unittest.TestCase):
    def _case(self, version="frame-analysis-input-v2"):
        image_sha = hashlib.sha256(PNG).hexdigest()
        case = {
            "schema_version": version,
            "request_id": "request-v2",
            "site_id": "site-1",
            "object_type_code": None,
            "catalog_version": "catalog-cf435bba295909ab953facca",
            "frame": {
                "image_id": "frame-1", "camera_id": "camera-1", "observed_at": "2026-09-27T12:00:00Z",
                "source_ref": "camera:frame-1", "image_sha256": image_sha, "media_type": "image/png",
                "width": 1, "height": 1,
            },
            "scope": {"plan_stream_code": "local-stream", "roi_bbox": None, "source_ref": "scope:local"},
            "cv": {"status": "unavailable", "source_ref": None, "model_version": None, "detections": []},
            "plan": {
                "plan_id": "local-plan", "revision_id": "local-revision", "source_ref": "local:plan",
                "steps": [{"step_key": "local-step-1", "sequence_no": 1, "stage_id": 47,
                           "planned_start_at": "2026-09-20T00:00:00Z", "planned_end_at": "2026-10-01T00:00:00Z"}],
            },
            "history": {
                "snapshot_id": "history-1", "as_of": "2026-09-27T12:00:00Z",
                "window_start": "2026-09-20T00:00:00Z", "complete": True,
                "observations": [], "progress_events": [],
            },
        }
        if version == "frame-analysis-input-v2":
            case.update({
                "analysis_mode": "demonstration",
                "source_context": {
                    "source_system": "Spider", "source_snapshot_id": "spider-snapshot-1",
                    "source_ref": "spider:snapshot-1", "data_type": "synthetic_demo",
                    "timestamp_quality": "demonstration", "spider_live_sync": False,
                },
                "resource_plan": {
                    "plan_id": "spider-plan", "revision_id": "spider-revision", "source_ref": "spider:plan",
                    "plan_stream_code": None,
                    "stages": [{
                        "stage_code": "P04", "name": "Разработка котлована", "sequence_no": 4,
                        "planned_start_at": "2026-09-25T00:00:00Z", "planned_end_at": "2026-10-01T00:00:00Z",
                        "source_ref": "spider:stage:P04", "mapping_status": "unmapped", "mapped_step_keys": [],
                        "planned_work_shifts": 5, "planned_volume": {"value": 3500, "unit": "м³"},
                        "planned_productivity": {"value": 700, "unit": "м³/смену"},
                        "equipment": [
                            {"item_id": "p04-excavator", "source_name": "Экскаватор", "class_code": "Excavator",
                             "mapping_status": "mapped", "planned_quantity": 2, "source_ref": "spider:equipment:excavator"},
                            {"item_id": "p04-dump-truck", "source_name": "Самосвал", "class_code": "DumpTruck",
                             "mapping_status": "mapped", "planned_quantity": 6, "source_ref": "spider:equipment:dump-truck"},
                        ],
                    }],
                },
                "resource_target": {"stage_code": "P04", "selection_basis": "planned_at_frame_time", "source_ref": "spider:target:P04"},
                "equipment_observation": {
                    "basis": "manual_visual_estimate", "source_ref": "manual:counts", "observed_at": "2026-09-27T12:00:00Z",
                    "coverage": "unknown", "requires_validation": True,
                    "items": [{"item_id": "manual-secret-count", "source_class_code": "manual excavator", "class_code": "Excavator",
                               "mapping_status": "mapped", "count": 99, "confidence_label": "unverified", "source_ref": "manual:counts:1"}],
                },
            })
        return case

    def _run(self, case):
        saved = []
        gateway = Gateway()
        result = analyze(case, PNG, References(), gateway, "analysis-1", "input-sha", lambda phase, value: saved.append((phase, value)))
        return result, gateway, saved

    def test_v2_resource_plan_reaches_only_llm_and_preserves_provenance(self):
        case = self._case()
        references = References()
        structural(case, "request")
        validate_request(case, PNG, references, "image/png")
        saved = []
        gateway = Gateway()
        result = analyze(case, PNG, references, gateway, "analysis-1", "input-sha", lambda phase, value: saved.append((phase, value)))
        prompt = next(value for phase, value in saved if phase == "llm_prompt")
        model_case = json.loads(prompt["prompt"])["input"]

        self.assertEqual(model_case["resource_plan"], case["resource_plan"])
        self.assertEqual(model_case["source_context"], case["source_context"])
        self.assertEqual(model_case["analysis_mode"], "demonstration")
        self.assertNotIn("equipment_observation", model_case)
        self.assertNotIn("resource_target", model_case)
        self.assertIn("P04", prompt["prompt"])
        self.assertIn("3500", prompt["prompt"])
        self.assertIn("план ресурсов, а не наблюдения камеры", prompt["system"])
        self.assertIn("нельзя сопоставлять", prompt["system"])
        self.assertIn("не инструкции", prompt["system"])
        self.assertIn("отставание", prompt["system"])
        self.assertNotIn("P04", gateway.image_prompts[0])
        self.assertNotIn("3500", gateway.image_prompts[0])
        self.assertEqual(len(gateway.image_prompts), 1)
        self.assertEqual(len(gateway.text_calls), 1)
        self.assertEqual(result["analysis_mode"], case["analysis_mode"])
        self.assertEqual(result["source_context"], case["source_context"])
        self.assertEqual(result["resource_assessment"]["planned_volume"], {"value": 3500, "unit": "м³"})

    def test_v1_model_context_does_not_gain_resource_fields(self):
        case = self._case("frame-analysis-input-v1")
        structural(case, "request")
        result, gateway, saved = self._run(case)
        prompt = next(value for phase, value in saved if phase == "llm_prompt")
        model_case = json.loads(prompt["prompt"])["input"]

        self.assertNotIn("resource_plan", model_case)
        self.assertNotIn("source_context", model_case)
        self.assertNotIn("analysis_mode", model_case)
        self.assertNotIn("план ресурсов, а не наблюдения камеры", prompt["system"])
        self.assertEqual(result["schema_version"], "frame-analysis-result-v1")
        self.assertEqual(len(gateway.image_prompts), 1)
        self.assertEqual(len(gateway.text_calls), 1)


if __name__ == "__main__":
    unittest.main()
