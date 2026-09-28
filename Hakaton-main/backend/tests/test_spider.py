"""Camera Stage Monitor adapter: all source traffic uses httpx.MockTransport."""

import asyncio
import copy
import hashlib
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select

from app.api import spider as spider_api
from app.config import get_settings
from app.api import work as work_api
from app.db import SessionLocal, utcnow
from app.models import SpiderConnection, SpiderImport, SpiderObservationAsset, SpiderSnapshot
from app.security import decrypt_secret
from app.services.spider import SpiderError, import_source, prepare_observation
from tests.conftest import login_as

pytestmark = pytest.mark.anyio


def _envelope(items, warning="demo warning"):
    return {"api_version": "1.0.0", "data_source": "local_poc_files", "data_type": "synthetic_demo", "warning": warning, "items": items, "count": len(items) if isinstance(items, list) else None}


def _documents() -> dict[str, dict]:
    stages = [
        {"code": "P03", "name": "Бурение", "start": "2026-09-15T09:00:00+03:00", "finish": "2026-09-22T09:00:00+03:00", "planned_work_shifts": 5, "planned_volume": 30.0, "volume_unit": "свай", "planned_productivity": {"value": 6.0, "unit": "свай/смену"}, "equipment": [{"name": "Буровая установка", "quantity": 1}]},
        {"code": "P04", "name": "Разработка котлована", "start": "2026-09-22T09:00:00+03:00", "finish": "2026-09-29T09:00:00+03:00", "planned_work_shifts": 5, "planned_volume": 3500.0, "volume_unit": "м³", "planned_productivity": {"value": 700.0, "unit": "м³/смену"}, "equipment": [{"name": "Экскаватор", "quantity": 2}, {"name": "Самосвал", "quantity": 6}]},
    ]
    equipment = [{**item, "stage": item.pop("code"), "stage_name": item.pop("name")} for item in copy.deepcopy(stages)]
    observations = [{"observation": "OBS01", "timestamp": "16.09.2026 09:00", "image": "Screenshot_8.png", "observed_stage": "P03", "timestamp_iso": "2026-09-16T09:00:00+03:00", "image_url": "/api/v1/images/OBS01_Screenshot_8.png"}]
    annotations = [{"observation": "OBS01", "observed_stage_code": "P03", "image_url": "/api/v1/images/OBS01_Screenshot_8.png", "equipment": [{"class_code": "drilling_rig", "class_name_ru": "Буровая установка", "count": 2, "confidence": "high"}]}]
    photo_equipment = _envelope(annotations, "manual annotation warning")
    photo_equipment.update({"annotation_type": "manual_visual_estimate", "requires_validation": True})
    comparisons = [{"observation": "OBS01", "planned_stage": "P03", "observed_stage": "P03", "stage_delta": 0, "status": "OK"}]
    return {
        "/api/v1/stages": _envelope(stages), "/api/v1/equipment": _envelope(equipment),
        "/api/v1/observations": _envelope(observations), "/api/v1/photo-equipment": photo_equipment,
        "/api/v1/comparisons": _envelope(comparisons),
    }


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (3, 2), "red").save(output, "JPEG")
    return output.getvalue()


def _transport(
    documents: dict[str, dict],
    image: bytes,
    *,
    plan_status: int = 200,
    plan_timestamp: str = "2026-09-16T09:00:00+03:00",
    calls: list[str] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if request.url.path == "/api/v1/images/OBS01_Screenshot_8.png":
            return httpx.Response(200, content=image, headers={"content-type": "image/jpeg"})
        if request.url.path == "/api/v1/plan-at":
            if plan_status != 200:
                return httpx.Response(plan_status, json={"detail": "invalid_timestamp"})
            assert request.url.params["timestamp"] == "2026-09-16T09:00:00+03:00"
            return httpx.Response(200, json=_envelope({"timestamp": plan_timestamp, "planned_stage": documents["/api/v1/stages"]["items"][0]}))
        payload = documents.get(request.url.path)
        return httpx.Response(200, content=json.dumps(payload, ensure_ascii=False).encode()) if payload else httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture
def spider_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "camera_stage_monitor_url", "http://spider.test")
    monkeypatch.setattr(settings, "camera_stage_monitor_token", None)
    monkeypatch.setattr(settings, "camera_stage_monitor_max_retries", 0)
    return settings


async def test_import_is_content_addressed_and_rejects_partial_revision(client, spider_settings):
    docs, photo = _documents(), _jpeg()
    first = await import_source(SessionLocal, "s1", transport=_transport(docs, photo))
    again = await import_source(SessionLocal, "s1", transport=_transport(docs, photo))
    assert first.status == again.status == "succeeded" and first.snapshot_id == again.snapshot_id
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(SpiderSnapshot)) == 1
        snapshot = await session.get(SpiderSnapshot, first.snapshot_id)
        assert snapshot is not None
        p04 = next(stage for stage in snapshot.resources["stages"] if stage["code"] == "P04")
        assert (p04["planned_volume"], p04["planned_productivity"], [x["planned_quantity"] for x in p04["equipment"]]) == ({"value": 3500.0, "unit": "м³"}, {"value": 700.0, "unit": "м³/смену"}, [2, 6])
        assert all(x["unit_productivity"] is None for x in p04["equipment"])
        assert json.loads(snapshot.documents["/api/v1/stages"]["body"])["items"][1]["code"] == "P04"

    changed = copy.deepcopy(docs)
    changed["/api/v1/stages"]["items"][1]["equipment"][1]["quantity"] = 7
    changed["/api/v1/equipment"]["items"][1]["equipment"][1]["quantity"] = 7
    updated = await import_source(SessionLocal, "s1", transport=_transport(changed, photo))
    assert updated.snapshot_id != first.snapshot_id
    broken = copy.deepcopy(changed)
    broken["/api/v1/equipment"]["items"][1]["equipment"][1]["quantity"] = 6
    failed = await import_source(SessionLocal, "s1", transport=_transport(broken, photo))
    assert failed.status == "failed" and failed.snapshot_id is None and failed.error_code == "source_resource_conflict"
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(SpiderSnapshot)) == 2


async def test_prepare_persists_verified_photo_and_retries_only_target(client, spider_settings):
    docs, photo, calls = _documents(), _jpeg(), []
    imported = await import_source(SessionLocal, "s1", transport=_transport(docs, photo))
    failed = await prepare_observation(SessionLocal, "s1", imported.snapshot_id, "OBS01", transport=_transport(docs, photo, plan_status=400, calls=calls))
    assert failed.target_document is None and failed.target_error == "source_http_400"
    assert any("images/OBS01" in url for url in calls) and any("plan-at" in url for url in calls)
    calls.clear()
    prepared = await prepare_observation(SessionLocal, "s1", imported.snapshot_id, "OBS01", transport=_transport(docs, photo, calls=calls))
    assert calls and all("plan-at" in url for url in calls)  # error replay does not fetch photo again
    assert prepared.target_document["code"] == "P03"
    async with SessionLocal() as session:
        asset = await session.get(SpiderObservationAsset, prepared.id)
        assert asset is not None and len(asset.target_attempts) == 2
    calls.clear()
    cached = await prepare_observation(SessionLocal, "s1", imported.snapshot_id, "OBS01", transport=_transport(docs, photo, calls=calls))
    assert cached.id == prepared.id and calls == []


async def test_prepare_rejects_plan_stage_or_timestamp_outside_snapshot(client, spider_settings):
    documents, photo = _documents(), _jpeg()
    imported = await import_source(SessionLocal, "s1", transport=_transport(documents, photo))
    changed_target = copy.deepcopy(documents)
    changed_target["/api/v1/stages"]["items"][0]["planned_volume"] = 31.0
    resource_conflict = await prepare_observation(
        SessionLocal, "s1", imported.snapshot_id, "OBS01", transport=_transport(changed_target, photo)
    )
    assert resource_conflict.target_document is None and resource_conflict.target_error == "source_revision_conflict"
    timestamp_conflict = await prepare_observation(
        SessionLocal,
        "s1",
        imported.snapshot_id,
        "OBS01",
        transport=_transport(documents, photo, plan_timestamp="2026-09-16T10:00:00+03:00"),
    )
    assert timestamp_conflict.target_document is None and timestamp_conflict.target_error == "source_revision_conflict"
    async with SessionLocal() as session:
        asset = await session.get(SpiderObservationAsset, resource_conflict.id)
        assert asset is not None and len(asset.target_attempts) == 2


async def test_prepare_serializes_attempts_and_never_replaces_wrong_digest(client, spider_settings):
    documents, photo = _documents(), _jpeg()
    imported = await import_source(SessionLocal, "s1", transport=_transport(documents, photo))
    first, second = await asyncio.gather(
        prepare_observation(SessionLocal, "s1", imported.snapshot_id, "OBS01", transport=_transport(documents, photo, plan_status=400)),
        prepare_observation(SessionLocal, "s1", imported.snapshot_id, "OBS01", transport=_transport(documents, photo, plan_status=400)),
    )
    assert first.id == second.id
    async with SessionLocal() as session:
        asset = await session.get(SpiderObservationAsset, first.id)
        assert asset is not None and len(asset.target_attempts) == 2

    revised = copy.deepcopy(documents)
    revised["/api/v1/stages"]["items"][1]["equipment"][1]["quantity"] = 7
    revised["/api/v1/equipment"]["items"][1]["equipment"][1]["quantity"] = 7
    different_snapshot = await import_source(SessionLocal, "s1", transport=_transport(revised, photo))
    path = spider_settings.data_dir / "spider" / "images" / f"{hashlib.sha256(photo).hexdigest()}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not the source image")
    with pytest.raises(SpiderError, match="source_storage_conflict"):
        await prepare_observation(SessionLocal, "s1", different_snapshot.snapshot_id, "OBS01", transport=_transport(revised, photo))
    assert path.read_bytes() == b"not the source image"
    path.write_bytes(photo)


async def test_router_protects_source_and_exposes_resource_status(client, spider_settings, monkeypatch):
    docs, photo = _documents(), _jpeg()

    async def mocked_import(_factory, site_id: str):
        return await import_source(SessionLocal, site_id, transport=_transport(docs, photo))

    monkeypatch.setattr(spider_api, "import_source", mocked_import)

    async def mocked_prepare(_factory, site_id: str, snapshot_id: str, observation_id: str):
        return await prepare_observation(
            SessionLocal, site_id, snapshot_id, observation_id, transport=_transport(docs, photo)
        )

    monkeypatch.setattr(spider_api, "prepare_observation", mocked_prepare)
    manager, foreman = await login_as(client, "manager"), await login_as(client, "foreman")
    before = await client.get("/api/sites/s1/spider", headers=manager)
    assert before.status_code == 200 and before.json()["limitations"] == ["source_not_imported"]
    assert (await client.post("/api/sites/s1/spider/import", headers=foreman)).status_code == 403
    imported = await client.post("/api/sites/s1/spider/import", headers=manager)
    assert imported.status_code == 200, imported.text
    shown = await client.get("/api/sites/s1/spider", headers=manager)
    body = shown.json()
    assert body["snapshot"]["resources"]["stages"][1]["equipment"][1]["plannedQuantity"] == 6
    assert body["snapshot"]["manualAnnotationType"] == "manual_visual_estimate"
    assert body["snapshot"]["manualRequiresValidation"] is True
    assert body["snapshot"]["manualAnnotations"][0]["equipment"][0]["confidence"] == "high"
    assert "annotation_type" not in body["snapshot"]["manualAnnotations"][0]
    assert "requires_validation" not in body["snapshot"]["manualAnnotations"][0]
    assert "resourceAnalysis" not in body
    work = await client.get("/api/sites/s1/work-analysis", headers=manager)
    assert work.status_code == 200 and work.json()["sourceConfigured"] is True and "resourceAnalysis" not in work.json()
    prepared = await client.post(
        "/api/sites/s1/spider/observations/OBS01/prepare",
        headers=manager,
        json={"snapshotId": imported.json()["snapshotId"]},
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["target"]["code"] == "P03"
    image = await client.get(prepared.json()["imageUrl"], headers=manager)
    assert image.status_code == 200 and image.content == photo and image.headers["content-type"] == "image/jpeg"
    assert (await client.get(f"/api/sites/s2/spider/images/{prepared.json()['id']}", headers=manager)).status_code == 404




def test_selected_frame_resource_evidence_is_typed_sanitized_and_never_falls_back_to_another_request():
    selected_metadata = {
        "schema_version": "frame-analysis-input-v2",
        "source_context": {"source_ref": "https://spider.test/private?token=do-not-show"},
        "resource_plan": {
            "stages": [
                {
                    "name": "Разработка котлована",
                    "equipment": [{"source_name": "Экскаватор", "planned_quantity": 2}],
                    "evidence/name": "selected-only",
                }
            ]
        },
        "cv": {"detections": [{"class_code": "Excavator"}]},
    }
    selected = type(
        "Request",
        (),
        {
            "metadata_json": json.dumps(selected_metadata),
            "notes": [],
            "trigger": "schedule",
            "site_id": "s1",
            "observed_at": utcnow(),
        },
    )()
    other_request = type(
        "Request",
        (),
        {
            "metadata_json": json.dumps(
                {
                    **selected_metadata,
                    "resource_plan": {
                        "stages": [{"name": "Другой объект", "equipment": [{"source_name": "Самосвал", "planned_quantity": 99}]}]
                    },
                }
            ),
            "notes": [],
            "trigger": "schedule",
            "site_id": "s2",
            "observed_at": utcnow(),
        },
    )()
    assessment = {
        "status": "demonstration",
        "reason_codes": ["demonstration_mode"],
        "stage_code": None,
        "selection_basis": None,
        "basis": "cv_detections",
        "coverage": "unknown",
        "equipment_items": [
            {
                "item_id": "excavator",
                "class_code": "Excavator",
                "planned_quantity": 2,
                "visible_count": 1,
                "visible_count_delta": None,
                "status": "unknown",
                "reason_codes": ["unknown_coverage"],
                "evidence_refs": ["/resource_plan/stages/0/equipment/0", "/cv/detections/0"],
            }
        ],
        "planned_volume": None,
        "planned_work_shifts": None,
        "planned_productivity": None,
        "actual_volume": None,
        "actual_productivity": None,
        "evidence_refs": [
            "/resource_plan/stages/0/equipment/0",
            "/source_context/source_ref",
            "/resource_plan/stages/0/evidence~1name",
            "/missing",
        ],
        "limitations": ["Демонстрационный источник."],
    }
    row = type(
        "Result",
        (),
        {
            "state": "done",
            "finished_at": utcnow(),
            "outcome": "insufficient_evidence",
            "result": {
                "schema_version": "frame-analysis-result-v2",
                "analysis_mode": "demonstration",
                "resource_assessment": assessment,
                "versions": {},
                "limitations": [],
                "current_work": {"work_groups": []},
                "transition": {"status": "not_evaluated"},
                "schedule": {"status": "not_evaluated"},
            },
            "error_code": None,
            "error": None,
        },
    )()

    answer = work_api._answer("deterministic", selected, row, work_api._Names([], None), newer_pending=False, spider_stale=False)
    ignored = work_api._answer("deterministic", other_request, row, work_api._Names([], None), newer_pending=False, spider_stale=False)
    retained = type(
        "Request",
        (),
        {"metadata_json": None, "notes": [], "trigger": "schedule", "observed_at": selected.observed_at},
    )()
    unavailable = work_api._answer("deterministic", retained, row, work_api._Names([], None), newer_pending=False, spider_stale=False)

    assert answer.resource_assessment is not None and answer.analysis_mode == "demonstration"
    projection = answer.model_dump(by_alias=True)
    assert projection["resourceAssessment"]["equipmentItems"][0]["visibleCount"] == 1
    assert projection["resourceEvidence"][0]["available"] is True
    assert answer.resource_assessment.equipment_items[0].planned_quantity == 2
    assert [item.pointer for item in answer.resource_evidence] == [
        "/resource_plan/stages/0/equipment/0",
        "/source_context/source_ref",
        "/resource_plan/stages/0/evidence~1name",
        "/missing",
        "/cv/detections/0",
    ]
    assert [item.available for item in answer.resource_evidence] == [True, True, True, False, True]
    descriptions = " ".join(item.description for item in answer.resource_evidence)
    assert "Разработка котлована" in descriptions and "Экскаватор" in descriptions
    assert "https://" not in descriptions and "token=" not in descriptions and "Самосвал" not in descriptions
    assert "Другой объект" in " ".join(item.description for item in ignored.resource_evidence)
    assert all(not item.available and item.description == "Исходные данные доказательства недоступны" for item in unavailable.resource_evidence)



async def test_site_connection_redacts_token_and_hides_old_origin_imports(client, spider_settings):
    manager, foreman = await login_as(client, "manager"), await login_as(client, "foreman")
    shown = await client.get("/api/sites/s1/spider/connection", headers=manager)
    assert shown.json() == {"url": "http://spider.test", "hasToken": False, "configured": True, "custom": False}
    assert (await client.put("/api/sites/s1/spider/connection", headers=foreman, json={"url": "http://one.test"})).status_code == 403
    saved = await client.put(
        "/api/sites/s1/spider/connection", headers=manager, json={"url": "http://one.test/", "token": "first-secret"}
    )
    assert saved.json() == {"url": "http://one.test", "hasToken": True, "configured": True, "custom": True}
    assert "first-secret" not in saved.text
    async with SessionLocal() as session:
        connection = await session.get(SpiderConnection, "s1")
        assert connection is not None and decrypt_secret(connection.token_enc) == "first-secret"
    # Omitting a token retains it only for the same origin.
    assert (await client.put("/api/sites/s1/spider/connection", headers=manager, json={"url": "http://one.test"})).json()["hasToken"]
    changed = await client.put("/api/sites/s1/spider/connection", headers=manager, json={"url": "https://two.test"})
    assert changed.json() == {"url": "https://two.test", "hasToken": False, "configured": True, "custom": True}
    async with SessionLocal() as session:
        connection = await session.get(SpiderConnection, "s1")
        assert connection is not None and connection.token_enc is None
        session.add(
            SpiderImport(
                id="old-origin",
                site_id="s1",
                source_url="http://one.test",
                status="failed",
                started_at=utcnow(),
                finished_at=utcnow(),
                fetches=[],
                partial_documents={},
                error_code="source_network_error",
                error_message="source_network_error",
            )
        )
        await session.commit()
    status_body = (await client.get("/api/sites/s1/spider", headers=manager)).json()
    assert status_body["lastImport"] is None and status_body["snapshot"] is None
    documents, requests = _documents(), []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = documents.get(request.url.path)
        assert payload is not None
        return httpx.Response(200, content=json.dumps(payload, ensure_ascii=False).encode())

    imported = await import_source(SessionLocal, "s1", transport=httpx.MockTransport(handler))
    assert imported.status == "succeeded"
    assert all(str(request.url).startswith("https://two.test/") for request in requests)
    assert all(request.headers.get("authorization") is None for request in requests)
    assert (await client.put("/api/sites/s1/spider/connection", headers=manager, json={"url": "https://two.test/path"})).status_code == 422

async def test_unconfigured_source_rejects_without_transport(client, spider_settings, monkeypatch):
    monkeypatch.setattr(spider_settings, "camera_stage_monitor_url", None)
    with pytest.raises(SpiderError, match="source_not_configured"):
        await import_source(
            SessionLocal,
            "s1",
            transport=httpx.MockTransport(lambda request: pytest.fail(f"unexpected request: {request.url}")),
        )
async def test_cross_origin_observation_url_is_never_requested(client, spider_settings):
    docs, photo = _documents(), _jpeg()
    docs["/api/v1/observations"]["items"][0]["image_url"] = "https://elsewhere.test/photo.jpg"
    imported = await import_source(SessionLocal, "s1", transport=_transport(docs, photo))
    with pytest.raises(SpiderError, match="unsafe_image_url"):
        await prepare_observation(SessionLocal, "s1", imported.snapshot_id, "OBS01", transport=_transport(docs, photo))
