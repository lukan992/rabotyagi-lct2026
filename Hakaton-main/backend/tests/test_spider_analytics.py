"""Persisted Spider plans become a bounded per-service analytics context."""

import copy
import hashlib
import json
import sys
from datetime import timedelta

import httpx
import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import select

from app import mock_analytics
from app.config import BASE_DIR, get_settings
from app.db import SessionLocal, utcnow
from app.models import AnalyticsRequest, AnalyticsResult, Camera, SpiderConnection, SpiderImport, SpiderSnapshot
from app.services.analytics import runner
from app.services.analytics.client import ServiceClient
from app.services.analytics.request import input_fingerprint, serialize, spider_context
from app.services.analytics.result import check_result
from app.services.analytics.runner import Analytics
from app.services.spider import SpiderConnectionConfig, connection_fingerprint, import_source
from tests.conftest import check, feed, login_as
from tests.test_spider import _documents as spider_documents
from tests.test_spider import _jpeg as spider_jpeg
from tests.test_spider import _transport as spider_transport

pytestmark = pytest.mark.anyio
V2_SCHEMA = json.loads((BASE_DIR / "docs" / "frame-analysis-v2.schema.json").read_text(encoding="utf-8"))


def _fingerprint(origin: str) -> str:
    return connection_fingerprint(SpiderConnectionConfig(origin=origin, token=None, custom=True))


def conforms_v2(name: str, message: dict) -> list[str]:
    schema = {"$schema": V2_SCHEMA["$schema"], "$defs": V2_SCHEMA["$defs"], "$ref": f"#/$defs/{name}"}
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [f"{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in validator.iter_errors(message)]


def _validate_with_vendor_contract(built) -> None:
    """Exercise the supplied wheel too, while production remains dependency-free."""
    wheel = BASE_DIR.parent.parent / "service-release_1" / "service-release" / "wheels" / (
        "lct_construction_analytics-0.5.0-py3-none-any.whl"
    )
    if not wheel.is_file():
        return
    sys.path.insert(0, str(wheel))
    try:
        from construction_analytics.contracts import validate_request
    except ModuleNotFoundError:
        return
    finally:
        sys.path.pop(0)
    metadata = copy.deepcopy(built.metadata)
    classes = {
        detection["class_code"]
        for block in [metadata["cv"], *(item["cv"] for item in metadata["history"]["observations"])]
        for detection in block["detections"]
    }
    references = type(
        "References",
        (),
        {
            "catalog_version": metadata["catalog_version"],
            "equipment_codes": classes,
            "stages": {
                step["stage_id"]: {
                    "stage_kind": "concrete",
                    "object_type_codes": [metadata["object_type_code"]],
                }
                for step in (metadata["plan"] or {"steps": []})["steps"]
            },
        },
    )()
    validate_request(metadata, built.image.data, references, built.image.media_type)


def _multipart_parts(request: httpx.Request) -> dict[str, bytes]:
    boundary = request.headers["content-type"].split("boundary=", 1)[1].encode()
    parts = {}
    for part in request.content.split(b"--" + boundary)[1:-1]:
        head, _, content = part.removeprefix(b"\r\n").removesuffix(b"\r\n").partition(b"\r\n\r\n")
        parts[head.split(b'name="')[1].split(b'"')[0].decode()] = content
    return parts


def _v2_answer(request: httpx.Request) -> dict:
    parts = _multipart_parts(request)
    metadata = json.loads(parts["metadata"])
    assert conforms_v2("request", metadata) == []
    result = mock_analytics.answer(
        "vlm_llm", metadata, hashlib.sha256(parts["metadata"] + b"\x00" + parts["image"]).hexdigest()
    )
    result.update(
        schema_version="frame-analysis-result-v2",
        analysis_id="an-vlm-v2-transport",
        analysis_mode=metadata["analysis_mode"],
        source_context=metadata["source_context"],
        resource_assessment={
            "status": "not_evaluated",
            "reason_codes": ["no_independent_measurements", "vlm_resources_not_evaluated"],
            "stage_code": None,
            "selection_basis": None,
            "basis": metadata["equipment_observation"]["basis"],
            "coverage": metadata["equipment_observation"]["coverage"],
            "equipment_items": [],
            "planned_volume": None,
            "planned_work_shifts": None,
            "planned_productivity": None,
            "actual_volume": None,
            "actual_productivity": None,
            "evidence_refs": ["/source_context", "/equipment_observation"],
            "limitations": ["Числовое сравнение ресурсов выполняет только deterministic."],
        },
    )
    result["versions"]["resource_rules_version"] = None
    assert conforms_v2("result", result) == []
    return result

def _v2_capabilities(service: str) -> dict:
    return {
        "schema_version": "frame-analysis-capabilities-v2",
        "service": service,
        "service_version": "0.5.0",
        "input_version": "frame-analysis-input-v2",
        "result_version": "frame-analysis-result-v2",
        "catalog_version": "catalog-test",
        "limits": {},
        "extensions": [],
        "resource_rules_version": "resource-rules-v1" if service == "deterministic" else None,
        "analysis_modes": ["demonstration", "operational"],
    }


def _deterministic_v2_answer(request: httpx.Request) -> dict:
    parts = _multipart_parts(request)
    metadata = json.loads(parts["metadata"])
    result = mock_analytics.answer(
        "deterministic", metadata, hashlib.sha256(parts["metadata"] + b"\x00" + parts["image"]).hexdigest()
    )
    result.update(
        schema_version="frame-analysis-result-v2",
        analysis_id="an-deterministic-v2-transport",
        analysis_mode=metadata["analysis_mode"],
        source_context=metadata["source_context"],
        resource_assessment={
            "status": "demonstration",
            "reason_codes": ["demonstration_mode", "no_resource_target"],
            "stage_code": None,
            "selection_basis": None,
            "basis": metadata["equipment_observation"]["basis"],
            "coverage": metadata["equipment_observation"]["coverage"],
            "equipment_items": [],
            "planned_volume": None,
            "planned_work_shifts": None,
            "planned_productivity": None,
            "actual_volume": None,
            "actual_productivity": None,
            "evidence_refs": ["/source_context", "/equipment_observation"],
            "limitations": ["Ресурсный этап не был выбран."],
        },
    )
    result["versions"]["resource_rules_version"] = "resource-rules-v1"
    assert conforms_v2("result", result) == []
    return result


def _resources(quantity: int = 2) -> dict:
    return {
        "stages": [
            {
                "code": "P04",
                "name": "Разработка котлована",
                "start": "2026-09-22T09:00:00+03:00",
                "finish": "2026-09-29T09:00:00+03:00",
                "planned_work_shifts": 5,
                "planned_volume": {"value": 3500.0, "unit": "м³"},
                "planned_productivity": {"value": 700.0, "unit": "м³/смену"},
                "equipment": [
                    {"source_name": "Экскаватор", "planned_quantity": quantity, "unit_productivity": None, "limitations": []},
                    {"source_name": "Самосвал", "planned_quantity": 6, "unit_productivity": None, "limitations": []},
                ],
            }
        ],
        "limitations": ["source limitation"],
    }


async def test_spider_context_projects_latest_success_for_its_site(client):
    now = utcnow()
    async with SessionLocal() as session:
        snapshot = SpiderSnapshot(
            id="snapshot-s1",
            source_url="https://spider-s1.test",
            api_version="1.0.0",
            data_source="local_poc_files",
            data_type="synthetic_demo",
            warning="source warning",
            documents={"/api/v1/photo-equipment": {"manual": "must not leave database"}},
            resources=_resources(),
            resource_revision_id="revision-s1",
        )
        other = SpiderSnapshot(
            id="snapshot-s2",
            source_url="https://spider-s2.test",
            api_version="1.0.0",
            data_source="other",
            data_type=None,
            warning=None,
            documents={},
            resources=_resources(9),
            resource_revision_id="revision-s2",
        )
        session.add_all(
            [
                snapshot,
                other,
                SpiderConnection(site_id="s1", source_url=snapshot.source_url),
                SpiderConnection(site_id="s2", source_url=other.source_url),
            ]
        )
        await session.flush()
        session.add_all(
            [
                SpiderImport(
                    id="import-s1",
                    site_id="s1",
                    source_url=snapshot.source_url,
                    connection_fingerprint=_fingerprint(snapshot.source_url),
                    status="succeeded",
                    started_at=now - timedelta(minutes=2),
                    finished_at=now - timedelta(minutes=1),
                    snapshot_id=snapshot.id,
                ),
                SpiderImport(
                    id="import-s2",
                    site_id="s2",
                    source_url=other.source_url,
                    connection_fingerprint=_fingerprint(other.source_url),
                    status="succeeded",
                    started_at=now - timedelta(minutes=1),
                    finished_at=now,
                    snapshot_id=other.id,
                ),
            ]
        )
        await session.commit()

    async with SessionLocal() as session:
        context = await spider_context(session, "s1", now=now)

    assert context == {
        "snapshot_id": "snapshot-s1",
        "resource_revision_id": "revision-s1",
        "data_type": "synthetic_demo",
        "stale": False,
        "resources": _resources(),
    }
    assert "documents" not in context and "source_url" not in context


@pytest.fixture
def analytics_services(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "deterministic_service_url", "http://analytics.test/deterministic")
    monkeypatch.setattr(settings, "vlm_llm_service_url", "http://analytics.test/vlm_llm")
    analytics = Analytics(transport=httpx.ASGITransport(app=mock_analytics.app))
    monkeypatch.setattr(runner, "_analytics", analytics)
    return analytics


async def _save_import(
    session,
    *,
    site_id: str,
    snapshot_id: str,
    import_id: str,
    started_at,
    finished_at,
    status: str = "succeeded",
    normalization_version: str = "1",
    resources: dict | None = None,
) -> SpiderSnapshot:
    snapshot = SpiderSnapshot(
        id=snapshot_id,
        source_url=f"https://spider-{site_id}.test",
        api_version="1.0.0",
        data_source="local_poc_files",
        data_type="synthetic_demo",
        warning=None,
        documents={"/api/v1/comparisons": {"must_not": "be sent"}},
        resources=resources if resources is not None else _resources(),
        normalization_version=normalization_version,
        resource_revision_id=f"revision-{snapshot_id}",
    )
    session.add(snapshot)
    if await session.get(SpiderConnection, site_id) is None:
        session.add(SpiderConnection(site_id=site_id, source_url=snapshot.source_url))
    camera = await session.get(Camera, "c1")
    if camera is not None and camera.site_id == site_id:
        camera.spider_enabled = True
    await session.flush()
    session.add(
        SpiderImport(
            id=import_id,
            site_id=site_id,
            source_url=snapshot.source_url,
            connection_fingerprint=_fingerprint(snapshot.source_url),
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            snapshot_id=snapshot.id if status == "succeeded" else None,
        )
    )
    return snapshot


async def test_spider_stale_uses_last_success_and_new_success_replaces_it(client):
    now = utcnow()
    async with SessionLocal() as session:
        first = await _save_import(
            session,
            site_id="s1",
            snapshot_id="snapshot-a",
            import_id="import-a",
            started_at=now - timedelta(minutes=3),
            finished_at=now - timedelta(minutes=2),
        )
        session.add(
            SpiderImport(
                id="import-b",
                site_id="s1",
                source_url=first.source_url,
                connection_fingerprint=_fingerprint(first.source_url),
                status="failed",
                started_at=now - timedelta(minutes=1),
                finished_at=now - timedelta(minutes=1),
            )
        )
        await session.commit()
        stale = await spider_context(session, "s1", now=now)
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="snapshot-c",
            import_id="import-c",
            started_at=now,
            finished_at=now,
        )
        await session.commit()
        current = await spider_context(session, "s1", now=now)

    assert stale is not None and (stale["snapshot_id"], stale["stale"]) == ("snapshot-a", True)
    assert current is not None and (current["snapshot_id"], current["stale"]) == ("snapshot-c", False)


async def test_per_service_requests_keep_exact_distinct_v2_metadata(client, analytics_services):
    await feed("c1", "pit-loading")
    await check("s1")
    now = utcnow()
    async with SessionLocal() as session:
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="snapshot-v2",
            import_id="import-v2",
            started_at=now - timedelta(minutes=1),
            finished_at=now,
        )
        await session.commit()

    prepared = await analytics_services._prepare(
        "c1", trigger="manual", only_new=False, services=["deterministic", "vlm_llm"]
    )
    assert set(prepared) == {"deterministic", "vlm_llm"}
    deterministic, vlm = prepared["deterministic"], prepared["vlm_llm"]
    assert deterministic.metadata["schema_version"] == vlm.metadata["schema_version"] == "frame-analysis-input-v2"
    assert conforms_v2("request", deterministic.metadata) == []
    assert conforms_v2("request", vlm.metadata) == []
    assert vlm.metadata["analysis_mode"] == "demonstration"
    assert vlm.metadata["source_context"] == {
        "source_system": "spider",
        "source_snapshot_id": "snapshot-v2",
        "source_ref": "spider:snapshots/snapshot-v2/resource-revisions/revision-snapshot-v2",
        "data_type": "synthetic_demo",
        "timestamp_quality": "demonstration",
        "spider_live_sync": None,
    }
    assert deterministic.metadata["source_context"] == vlm.metadata["source_context"]
    assert vlm.metadata["resource_plan"]["plan_stream_code"] is None
    stage = vlm.metadata["resource_plan"]["stages"][0]
    assert stage["mapping_status"] == "unmapped" and stage["mapped_step_keys"] == []
    assert stage["equipment"][1]["planned_quantity"] == 6
    assert all(item["mapping_status"] == "unmapped" and item["class_code"] is None for item in stage["equipment"])
    assert vlm.metadata["resource_target"] is None
    assert vlm.metadata["equipment_observation"] == {
        "basis": "cv_detections",
        "source_ref": vlm.metadata["cv"]["source_ref"],
        "observed_at": vlm.metadata["frame"]["observed_at"],
        "coverage": "unknown",
        "requires_validation": False,
        "items": [],
    }
    wire_text = vlm.body.decode("utf-8")
    assert "spider.test" not in wire_text and "comparisons" not in wire_text and "must_not" not in wire_text
    assert deterministic.request_id != vlm.request_id

    async with SessionLocal() as session:
        rows = {
            row.results[0].service: row
            for row in await session.scalars(select(AnalyticsRequest).where(AnalyticsRequest.camera_id == "c1"))
        }
    assert set(rows) == {"deterministic", "vlm_llm"}
    for service, built in prepared.items():
        row = rows[service]
        assert row.metadata_json == built.body.decode("utf-8")
        assert row.input_sha256 == input_fingerprint(built.body, built.image.data) == built.input_sha256
        assert row.results[0].request_id == row.id == built.request_id


async def test_invalid_saved_spider_context_blocks_resource_requests(client, analytics_services):
    await feed("c1", "pit-loading")
    await check("s1")
    now = utcnow()
    async with SessionLocal() as session:
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="invalid-snapshot",
            import_id="invalid-import",
            started_at=now,
            finished_at=now,
            normalization_version="2",
        )
        await session.commit()

    prepared = await analytics_services._prepare(
        "c1", trigger="manual", only_new=False, services=["deterministic", "vlm_llm"]
    )
    assert prepared == {}
    assert "некорректный сохранённый импорт" in analytics_services.problems["s1"][1]
async def test_unordered_source_stages_are_sorted_for_vendor_sequence(client, analytics_services, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "camera_stage_monitor_url", "http://spider.test")
    monkeypatch.setattr(settings, "camera_stage_monitor_token", None)
    monkeypatch.setattr(settings, "camera_stage_monitor_max_retries", 0)
    documents = spider_documents()
    documents["/api/v1/stages"]["items"].reverse()
    async with SessionLocal() as session:
        camera = await session.get(Camera, "c1")
        assert camera is not None
        camera.spider_enabled = True
        await session.commit()
    imported = await import_source(SessionLocal, "s1", transport=spider_transport(documents, spider_jpeg()))
    assert imported.status == "succeeded"

    await feed("c1", "pit-loading")
    await check("s1")
    built = (await analytics_services._prepare("c1", trigger="manual", only_new=False, services=["vlm_llm"]))["vlm_llm"]
    stages = built.metadata["resource_plan"]["stages"]
    assert [(stage["stage_code"], stage["sequence_no"]) for stage in stages] == [("P03", 1), ("P04", 2)]
    p04 = stages[1]
    assert (p04["planned_volume"], p04["planned_productivity"]) == (
        {"value": 3500.0, "unit": "м³"},
        {"value": 700.0, "unit": "м³/смену"},
    )
    assert [(item["source_name"], item["planned_quantity"]) for item in p04["equipment"]] == [
        ("Экскаватор", 2),
        ("Самосвал", 6),
    ]


async def test_missing_spider_import_preserves_v1_with_durable_local_note(client, analytics_services):
    await feed("c1", "pit-loading")
    await check("s1")
    now = utcnow()
    async with SessionLocal() as session:
        camera = await session.get(Camera, "c1")
        assert camera is not None
        camera.spider_enabled = True
        session.add(
            SpiderImport(
                id="failed-only-import",
                site_id="s1",
                source_url="https://spider.test/s1",
                status="failed",
                started_at=now,
                finished_at=now,
                error_code="source_http_502",
                error_message="not available",
            )
        )
        await session.commit()

    prepared = await analytics_services._prepare(
        "c1", trigger="manual", only_new=False, services=["deterministic", "vlm_llm"]
    )
    assert {service: request.metadata["schema_version"] for service, request in prepared.items()} == {
        "deterministic": "frame-analysis-input-v1",
        "vlm_llm": "frame-analysis-input-v1",
    }
    assert all(request.notes.count("spider_context_unavailable") == 1 for request in prepared.values())
    async with SessionLocal() as session:
        rows = list(await session.scalars(select(AnalyticsRequest).where(AnalyticsRequest.camera_id == "c1")))
    assert {
        row.results[0].service: row.notes.count("spider_context_unavailable")
        for row in rows
    } == {"deterministic": 1, "vlm_llm": 1}


async def test_empty_spider_stages_preserve_provenance_without_fabricating_plan(client, analytics_services):
    await feed("c1", "pit-loading")
    await check("s1")
    now = utcnow()
    async with SessionLocal() as session:
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="empty-plan",
            import_id="empty-import",
            started_at=now,
            finished_at=now,
            resources={"stages": [], "limitations": ["none"]},
        )
        await session.commit()
    built = (await analytics_services._prepare("c1", trigger="manual", only_new=False, services=["vlm_llm"]))["vlm_llm"]
    assert conforms_v2("request", built.metadata) == []
    assert built.metadata["resource_plan"] is None
    assert built.metadata["resource_target"] is None
    vendor_result = mock_analytics.answer("vlm_llm", built.metadata, built.input_sha256)
    vendor_result.update(
        schema_version="frame-analysis-result-v2",
        analysis_mode=built.metadata["analysis_mode"],
        source_context=built.metadata["source_context"],
        resource_assessment={"status": "not_evaluated", "equipment_items": []},
    )
    async with SessionLocal() as session:
        row = await session.scalar(select(AnalyticsResult).where(AnalyticsResult.request_id == built.request_id))
        assert row is not None
        row.state, row.result, row.outcome, row.finished_at = "done", vendor_result, vendor_result["current_work"]["status"], now
        await session.commit()
    manager = await login_as(client, "manager")
    work = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()
    answer = next(item for item in work["cameras"][0]["answers"] if item["service"] == "vlm_llm")
    assert answer["limitations"][-2:] == [
        "Spider: дополнительный контекст; это не наблюдения камеры.",
        "Spider: источник synthetic_demo.",
    ]


async def test_invalid_resource_projection_blocks_all_selected_services(client, analytics_services):
    await feed("c1", "pit-loading")
    await check("s1")
    resources = _resources()
    resources["stages"][0]["planned_work_shifts"] = 0
    now = utcnow()
    async with SessionLocal() as session:
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="zero-shifts",
            import_id="zero-shifts-import",
            started_at=now,
            finished_at=now,
            resources=resources,
        )
        await session.commit()
    prepared = await analytics_services._prepare(
        "c1", trigger="manual", only_new=False, services=["deterministic", "vlm_llm"]
    )
    assert prepared == {}
    assert "нулевое количество плановых смен" in analytics_services.problems["s1"][1]


async def test_overlong_spider_stage_code_blocks_all_resource_requests(client, analytics_services):
    await feed("c1", "pit-loading")
    await check("s1")
    resources = _resources()
    resources["stages"][0]["code"] = "P" * 101
    now = utcnow()
    async with SessionLocal() as session:
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="overlong-stage",
            import_id="overlong-stage-import",
            started_at=now,
            finished_at=now,
            resources=resources,
        )
        await session.commit()
    prepared = await analytics_services._prepare(
        "c1", trigger="manual", only_new=False, services=["deterministic", "vlm_llm"]
    )
    assert prepared == {}
    assert "код ресурсного этапа" in analytics_services.problems["s1"][1]


async def test_work_view_keeps_spider_provenance_at_request_time(client, analytics_services):
    await feed("c1", "pit-loading")
    await check("s1")
    now = utcnow()
    async with SessionLocal() as session:
        snapshot = await _save_import(
            session,
            site_id="s1",
            snapshot_id="ui-snapshot",
            import_id="ui-success",
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
        )
        await session.commit()
    fresh = (await analytics_services._prepare("c1", trigger="manual", only_new=False, services=["vlm_llm"]))["vlm_llm"]
    fresh_result = mock_analytics.answer("vlm_llm", fresh.metadata, fresh.input_sha256)
    fresh_result.update(
        schema_version="frame-analysis-result-v2",
        analysis_mode=fresh.metadata["analysis_mode"],
        source_context=fresh.metadata["source_context"],
        resource_assessment={"status": "not_evaluated", "equipment_items": []},
    )
    original = json.loads(json.dumps(fresh_result))
    failed_at = utcnow()
    async with SessionLocal() as session:
        row = await session.scalar(select(AnalyticsResult).where(AnalyticsResult.request_id == fresh.request_id))
        assert row is not None
        row.state, row.result, row.outcome, row.finished_at = "done", fresh_result, fresh_result["current_work"]["status"], failed_at
        session.add(
            SpiderImport(
                id="ui-failed",
                site_id="s1",
                source_url=snapshot.source_url,
                connection_fingerprint=_fingerprint(snapshot.source_url),
                status="failed",
                started_at=failed_at,
                finished_at=failed_at,
            )
        )
        await session.commit()
    manager = await login_as(client, "manager")
    work = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()
    answer = next(item for item in work["cameras"][0]["answers"] if item["service"] == "vlm_llm")
    assert answer["limitations"][-2:] == [
        "Spider: дополнительный план и ресурсы; это не наблюдения камеры.",
        "Spider: источник synthetic_demo.",
    ]

    stale = (await analytics_services._prepare("c1", trigger="manual", only_new=False, services=["vlm_llm"]))["vlm_llm"]
    stale_result = mock_analytics.answer("vlm_llm", stale.metadata, stale.input_sha256)
    stale_result.update(
        schema_version="frame-analysis-result-v2",
        analysis_mode=stale.metadata["analysis_mode"],
        source_context=stale.metadata["source_context"],
        resource_assessment={"status": "not_evaluated", "equipment_items": []},
    )
    refreshed_at = utcnow()
    async with SessionLocal() as session:
        row = await session.scalar(select(AnalyticsResult).where(AnalyticsResult.request_id == stale.request_id))
        assert row is not None
        row.state, row.result, row.outcome, row.finished_at = "done", stale_result, stale_result["current_work"]["status"], refreshed_at
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="ui-refreshed",
            import_id="ui-refreshed-import",
            started_at=refreshed_at,
            finished_at=refreshed_at,
        )
        await session.commit()
    work = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()
    answer = next(item for item in work["cameras"][0]["answers"] if item["service"] == "vlm_llm")
    assert answer["limitations"][-3:] == [
        "Spider: дополнительный план и ресурсы; это не наблюдения камеры.",
        "Spider: источник synthetic_demo.",
        "Spider: используется последний успешный импорт; источник устарел.",
    ]
    async with SessionLocal() as session:
        stored = await session.scalar(select(AnalyticsResult).where(AnalyticsResult.request_id == fresh.request_id))
    assert stored is not None and stored.result == original

async def test_work_view_uses_refresh_staleness_at_request_time(client, analytics_services, monkeypatch):
    await feed("c1", "pit-loading")
    await check("s1")
    settings = get_settings()
    monkeypatch.setattr(settings, "camera_stage_monitor_refresh_seconds", 60.0)
    now = utcnow()
    async with SessionLocal() as session:
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="expired-snapshot",
            import_id="expired-import",
            started_at=now - timedelta(minutes=3),
            finished_at=now - timedelta(minutes=2),
        )
        await session.commit()
    built = (await analytics_services._prepare("c1", trigger="manual", only_new=False, services=["vlm_llm"]))["vlm_llm"]
    vendor_result = mock_analytics.answer("vlm_llm", built.metadata, built.input_sha256)
    vendor_result.update(
        schema_version="frame-analysis-result-v2",
        analysis_mode=built.metadata["analysis_mode"],
        source_context=built.metadata["source_context"],
        resource_assessment={"status": "not_evaluated", "equipment_items": []},
    )
    refreshed_at = utcnow()
    async with SessionLocal() as session:
        row = await session.scalar(select(AnalyticsResult).where(AnalyticsResult.request_id == built.request_id))
        assert row is not None
        row.state, row.result, row.outcome, row.finished_at = "done", vendor_result, vendor_result["current_work"]["status"], refreshed_at
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="fresh-snapshot",
            import_id="fresh-import",
            started_at=refreshed_at,
            finished_at=refreshed_at,
        )
        await session.commit()
    manager = await login_as(client, "manager")
    work = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()
    answer = next(item for item in work["cameras"][0]["answers"] if item["service"] == "vlm_llm")
    assert answer["limitations"][-3:] == [
        "Spider: дополнительный план и ресурсы; это не наблюдения камеры.",
        "Spider: источник synthetic_demo.",
        "Spider: используется последний успешный импорт; источник устарел.",
    ]


async def _prepared_v2(analytics_services: Analytics, service: str = "vlm_llm"):
    await feed("c1", "pit-loading")
    await check("s1")
    now = utcnow()
    async with SessionLocal() as session:
        await _save_import(
            session,
            site_id="s1",
            snapshot_id="snapshot-transport",
            import_id="import-transport",
            started_at=now - timedelta(minutes=1),
            finished_at=now,
        )
        await session.commit()
    return (await analytics_services._prepare("c1", trigger="manual", only_new=False, services=[service]))[service]


def _bind_resource_target(built, *, coverage: str, operational: bool = False) -> tuple[dict, dict]:
    metadata = built.metadata
    stage = metadata["resource_plan"]["stages"][0]
    metadata["resource_plan"]["plan_stream_code"] = metadata["scope"]["plan_stream_code"]
    metadata["resource_target"] = {
        "stage_code": stage["stage_code"],
        "selection_basis": "planned_at_frame_time",
        "source_ref": stage["source_ref"],
    }
    metadata["equipment_observation"]["coverage"] = coverage
    if operational:
        metadata["analysis_mode"] = "operational"
        metadata["source_context"].update(data_type="production", timestamp_quality="verified")
    built.body = serialize(metadata)
    built.input_sha256 = input_fingerprint(built.body, built.image.data)
    return metadata, stage


async def test_v2_transport_accepts_schema_valid_result(client, analytics_services):
    built = await _prepared_v2(analytics_services)
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("vlm_llm"))
        return httpx.Response(200, json=_v2_answer(request))
    service = ServiceClient("vlm_llm", "http://vlm.test", None, 30, transport=httpx.MockTransport(handler))
    reply = await service.analyze(
        request_id=built.request_id,
        site_id=built.metadata["site_id"],
        schema_version=built.metadata["schema_version"],
        metadata=built.body,
        image=built.image.data,
        media_type=built.image.media_type,
    )
    assert paths == ["/v2/capabilities", "/v2/analyze/frame"] and reply.state == "done"
    assert conforms_v2("result", reply.result) == []
    _validate_with_vendor_contract(built)
    assert check_result(reply.result, service="vlm_llm", metadata=built.metadata, input_sha256=built.input_sha256) is None


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("source_context", "source_snapshot_id"), "other-id", "source_context ответа не совпадает с запросом"),
        (("resource_assessment", "status"), "compared", "resource_assessment.status = not_evaluated"),
    ],
)
async def test_v2_result_with_changed_vendor_provenance_is_invalid_result(client, analytics_services, path, value, expected):
    built = await _prepared_v2(analytics_services)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("vlm_llm"))
        result = _v2_answer(request)
        result[path[0]][path[1]] = value
        return httpx.Response(200, json=result)

    analytics_services.clients["vlm_llm"] = ServiceClient(
        "vlm_llm", "http://vlm.test", None, 30, transport=httpx.MockTransport(handler)
    )
    reply, _ = await analytics_services._ask("vlm_llm", built)
    assert (reply.state, reply.code) == ("error", "invalid_result")
    assert expected in reply.message


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(404, "spider_context_unsupported"), (405, "spider_context_unsupported"), (400, "invalid_request")],
)
async def test_v2_route_rejection_never_falls_back_to_v1(client, analytics_services, status, expected_code):
    built = await _prepared_v2(analytics_services)
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("vlm_llm"))
        return httpx.Response(status, json={"code": "invalid_request", "message": "old service", "retryable": False})

    service = ServiceClient("vlm_llm", "http://vlm.test", None, 30, transport=httpx.MockTransport(handler))
    reply = await service.analyze(
        request_id=built.request_id,
        site_id=built.metadata["site_id"],
        schema_version=built.metadata["schema_version"],
        metadata=built.body,
        image=built.image.data,
        media_type=built.image.media_type,
    )
    assert paths == ["/v2/capabilities", "/v2/analyze/frame"] and (reply.state, reply.http_status, reply.code) == (
        "error",
        status,
        expected_code,
    )
    if status in (404, 405):
        assert reply.message == "Сервис не поддерживает ресурсный контекст v2"
    else:
        assert reply.message == "old service"


async def test_lost_v2_response_uses_lookup_without_second_post(client, analytics_services):
    built = await _prepared_v2(analytics_services)
    posts, lookups = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("vlm_llm"))
        if request.method == "POST":
            posts.append(request)
            raise httpx.ReadTimeout("нет ответа", request=request)
        lookups.append(str(request.url))
        return httpx.Response(200, json=_v2_answer(posts[0]))

    service = ServiceClient("vlm_llm", "http://vlm.test", None, 30, transport=httpx.MockTransport(handler))
    reply = await service.analyze(
        request_id=built.request_id,
        site_id=built.metadata["site_id"],
        schema_version=built.metadata["schema_version"],
        metadata=built.body,
        image=built.image.data,
        media_type=built.image.media_type,
    )
    assert reply.state == "done" and len(posts) == 1
    assert lookups == [f"http://vlm.test/v2/analyses/by-request/{built.request_id}?site_id=s1"]


async def test_deterministic_v2_accepts_valid_resource_assessment(client, analytics_services):
    built = await _prepared_v2(analytics_services, "deterministic")
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("deterministic"))
        return httpx.Response(200, json=_deterministic_v2_answer(request))

    service = ServiceClient("deterministic", "http://deterministic.test", None, 30, transport=httpx.MockTransport(handler))
    reply = await service.analyze(
        request_id=built.request_id,
        site_id=built.metadata["site_id"],
        schema_version=built.metadata["schema_version"],
        metadata=built.body,
        image=built.image.data,
        media_type=built.image.media_type,
    )
    assert paths == ["/v2/capabilities", "/v2/analyze/frame"] and reply.state == "done"
    assert check_result(reply.result, service="deterministic", metadata=built.metadata, input_sha256=built.input_sha256) is None



async def test_deterministic_rejects_selected_stage_without_resource_target(client, analytics_services):
    built = await _prepared_v2(analytics_services, "deterministic")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("deterministic"))
        result = _deterministic_v2_answer(request)
        metadata = json.loads(_multipart_parts(request)["metadata"])
        stage = metadata["resource_plan"]["stages"][0]
        planned = stage["equipment"][0]
        result["resource_assessment"].update(
            stage_code=stage["stage_code"],
            selection_basis="planned_at_frame_time",
            planned_volume=stage["planned_volume"],
            planned_work_shifts=stage["planned_work_shifts"],
            planned_productivity=stage["planned_productivity"],
            equipment_items=[
                {
                    "item_id": planned["item_id"],
                    "class_code": planned["class_code"],
                    "planned_quantity": planned["planned_quantity"],
                    "visible_count": planned["planned_quantity"],
                    "visible_count_delta": 0,
                    "status": "visible_equal_plan",
                    "reason_codes": [],
                    "evidence_refs": ["/resource_plan/stages/0/equipment/0"],
                }
            ],
        )
        return httpx.Response(200, json=result)

    analytics_services.clients["deterministic"] = ServiceClient(
        "deterministic", "http://deterministic.test", None, 30, transport=httpx.MockTransport(handler)
    )
    reply, _ = await analytics_services._ask("deterministic", built)
    assert (reply.state, reply.code) == ("error", "invalid_result")
    assert "должны совпадать с ресурсной целью" in reply.message


async def test_deterministic_rejects_demonstration_delta_with_partial_coverage(client, analytics_services):
    built = await _prepared_v2(analytics_services, "deterministic")
    _bind_resource_target(built, coverage="partial_scope")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("deterministic"))
        result = _deterministic_v2_answer(request)
        metadata = json.loads(_multipart_parts(request)["metadata"])
        stage, planned = metadata["resource_plan"]["stages"][0], metadata["resource_plan"]["stages"][0]["equipment"][0]
        result["resource_assessment"].update(
            stage_code=stage["stage_code"],
            selection_basis="planned_at_frame_time",
            planned_volume=stage["planned_volume"],
            planned_work_shifts=stage["planned_work_shifts"],
            planned_productivity=stage["planned_productivity"],
            equipment_items=[{
                "item_id": planned["item_id"], "class_code": planned["class_code"],
                "planned_quantity": planned["planned_quantity"], "visible_count": planned["planned_quantity"],
                "visible_count_delta": 0, "status": "visible_equal_plan", "reason_codes": [],
                "evidence_refs": ["/resource_plan/stages/0/equipment/0"],
            }],
        )
        return httpx.Response(200, json=result)

    analytics_services.clients["deterministic"] = ServiceClient(
        "deterministic", "http://deterministic.test", None, 30, transport=httpx.MockTransport(handler)
    )
    reply, _ = await analytics_services._ask("deterministic", built)
    assert (reply.state, reply.code) == ("error", "invalid_result")
    assert "неполном охвате" in reply.message


async def test_deterministic_rejects_compared_rows_under_insufficient_evidence(client, analytics_services):
    built = await _prepared_v2(analytics_services, "deterministic")
    metadata, stage = _bind_resource_target(built, coverage="full_scope", operational=True)
    planned = stage["equipment"][0]
    stage["mapping_status"] = "mapped"
    stage["mapped_step_keys"] = [metadata["plan"]["steps"][0]["step_key"]]
    planned.update(mapping_status="mapped", class_code="Excavator")
    built.body = serialize(metadata)
    built.input_sha256 = input_fingerprint(built.body, built.image.data)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("deterministic"))
        result = _deterministic_v2_answer(request)
        metadata = json.loads(_multipart_parts(request)["metadata"])
        stage, planned = metadata["resource_plan"]["stages"][0], metadata["resource_plan"]["stages"][0]["equipment"][0]
        result["resource_assessment"].update(
            status="insufficient_evidence",
            reason_codes=["no_independent_measurements"],
            stage_code=stage["stage_code"],
            selection_basis="planned_at_frame_time",
            planned_volume=stage["planned_volume"],
            planned_work_shifts=stage["planned_work_shifts"],
            planned_productivity=stage["planned_productivity"],
            equipment_items=[{
                "item_id": planned["item_id"], "class_code": planned["class_code"],
                "planned_quantity": planned["planned_quantity"], "visible_count": planned["planned_quantity"],
                "visible_count_delta": 0, "status": "visible_equal_plan", "reason_codes": [],
                "evidence_refs": ["/resource_plan/stages/0/equipment/0"],
            }],
        )
        return httpx.Response(200, json=result)

    analytics_services.clients["deterministic"] = ServiceClient(
        "deterministic", "http://deterministic.test", None, 30, transport=httpx.MockTransport(handler)
    )
    reply, _ = await analytics_services._ask("deterministic", built)
    assert (reply.state, reply.code) == ("error", "invalid_result")
    assert "status = compared" in reply.message

@pytest.mark.parametrize(
    ("capability_response", "expected_code"),
    [
        (httpx.Response(503, json={"code": "unavailable"}), "resource_capabilities_unavailable"),
        (httpx.Response(200, json={}), "spider_context_unsupported"),
    ],
)
async def test_v2_capability_failure_is_explicit_and_never_posts(
    client, analytics_services, capability_response, expected_code
):
    built = await _prepared_v2(analytics_services)
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return capability_response

    service = ServiceClient("vlm_llm", "http://vlm.test", None, 30, transport=httpx.MockTransport(handler))
    reply = await service.analyze(
        request_id=built.request_id,
        site_id=built.metadata["site_id"],
        schema_version=built.metadata["schema_version"],
        metadata=built.body,
        image=built.image.data,
        media_type=built.image.media_type,
    )
    assert paths == ["/v2/capabilities"] and (reply.state, reply.code) == ("error", expected_code)


async def test_v2_capabilities_are_cached_between_posts(client, analytics_services):
    built = await _prepared_v2(analytics_services)
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v2/capabilities":
            return httpx.Response(200, json=_v2_capabilities("vlm_llm"))
        return httpx.Response(400, json={"code": "invalid_request", "retryable": False})

    service = ServiceClient("vlm_llm", "http://vlm.test", None, 30, transport=httpx.MockTransport(handler))
    for request_id in ("fa-cache-one", "fa-cache-two"):
        await service.analyze(
            request_id=request_id,
            site_id=built.metadata["site_id"],
            schema_version=built.metadata["schema_version"],
            metadata=built.body,
            image=built.image.data,
            media_type=built.image.media_type,
        )
    assert paths == ["/v2/capabilities", "/v2/analyze/frame", "/v2/analyze/frame"]
