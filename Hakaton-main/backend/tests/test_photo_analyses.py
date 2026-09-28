"""Independent uploaded-photo analysis never becomes a camera frame."""

import asyncio
import io
import json
import stat

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app import mock_analytics
from app.api import work as work_api
from app.config import get_settings
from app.db import SessionLocal
from app.models import AnalyticsRequest, Snapshot
from app.services import analysis as analysis_module
from app.services import detector as detector_module
from app.services.analytics import runner
from app.services.analytics.catalog import CatalogError
from app.services.analytics.images import photo_path
from app.services.analytics.runner import Analytics
from app.services.camera_client import mock_frame, normalize_frame_async
from tests.conftest import check, feed, login_as

pytestmark = pytest.mark.anyio


@pytest.fixture
def services(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "deterministic_service_url", "http://analytics.test/deterministic")
    monkeypatch.setattr(settings, "vlm_llm_service_url", "http://analytics.test/vlm_llm")
    monkeypatch.setitem(mock_analytics.DELAYS_S, "deterministic", 0)
    monkeypatch.setitem(mock_analytics.DELAYS_S, "vlm_llm", 0)
    analytics = Analytics(transport=httpx.ASGITransport(app=mock_analytics.app))
    monkeypatch.setattr(runner, "_analytics", analytics)
    return analytics


async def _completed(http, headers, site_id, photo_id):
    for _ in range(20):
        response = await http.get(f"/api/sites/{site_id}/photo-analyses/{photo_id}", headers=headers)
        assert response.status_code == 200, response.text
        if response.json()["answers"] and all(item["state"] != "pending" for item in response.json()["answers"]):
            return response
        await asyncio.sleep(0)
    return response


async def test_photo_is_private_camera_free_and_persists_normalized_image(client, services):
    headers = await login_as(client, "manager")
    original = mock_frame("pit-loading")
    expected = await normalize_frame_async(original, force_reencode=True)
    post = await client.post(
        "/api/sites/s1/photo-analyses",
        headers=headers,
        files={"image": ("loading.jpg", original, "image/jpeg")},
        data={"useSpider": "false"},
    )
    assert post.status_code == 201, post.text
    body = post.json()
    photo_id = body["id"]
    assert body["llmWaiting"] == 0
    assert photo_id.startswith("photo_") and body["imageUrl"].startswith("/api/sites/s1/photo-analyses/")
    image = await client.get(body["imageUrl"], headers=headers)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg" and image.content == expected
    assert image.headers["cache-control"] == "private, no-store"
    path = photo_path(photo_id)
    assert path is not None and stat.S_IMODE(path.stat().st_mode) == 0o600
    wrong_site = await client.get(f"/api/sites/s2/photo-analyses/{photo_id}", headers=headers)
    assert wrong_site.status_code == 404

    async with SessionLocal() as session:
        snapshot = await session.get(Snapshot, photo_id)
        assert snapshot is not None
        assert snapshot.source == "photo" and snapshot.camera_id is None and snapshot.image_url == f"photo:{photo_id}"
        assert snapshot.detections

    with Image.open(io.BytesIO(original)) as source:
        png = io.BytesIO()
        source.save(png, "PNG")
    png_post = await client.post(
        "/api/sites/s1/photo-analyses",
        headers=headers,
        files={"image": ("loading.png", png.getvalue(), "image/png")},
    )
    assert png_post.status_code == 201, png_post.text
    png_image = await client.get(png_post.json()["imageUrl"], headers=headers)
    assert png_image.headers["content-type"] == "image/jpeg"
    assert png_image.content == await normalize_frame_async(png.getvalue(), force_reencode=True)


async def test_photo_keeps_its_snapshot_when_newer_camera_frame_arrives(client, services):
    from app.services.camera_client import mock_frame

    headers = await login_as(client, "manager")
    post = await client.post(
        "/api/sites/s1/photo-analyses",
        headers=headers,
        files={"image": ("loading.jpg", mock_frame("pit-loading"), "image/jpeg")},
    )
    assert post.status_code == 201, post.text
    photo_id = post.json()["id"]

    await feed("c1", "pit-loading")
    await check("s1")
    done = await _completed(client, headers, "s1", photo_id)
    assert {answer["service"] for answer in done.json()["answers"]} == {"deterministic", "vlm_llm"}
    assert all(answer["state"] == "done" for answer in done.json()["answers"])

    async with SessionLocal() as session:
        requests = list(
            await session.scalars(select(AnalyticsRequest).where(AnalyticsRequest.snapshot_id == photo_id))
        )
        assert {request.camera_id for request in requests} == {None}
        assert {request.trigger for request in requests} == {"photo"}
        assert all(json.loads(request.metadata_json)["frame"]["image_id"] == photo_id for request in requests)
        assert all(json.loads(request.metadata_json)["frame"]["camera_id"] == f"photo_{photo_id}" for request in requests)


async def test_photo_spider_is_opt_in_and_does_not_depend_on_camera_flag(client, services, monkeypatch):
    from app.services.camera_client import mock_frame

    calls = []

    async def context(session, site_id, *, now):
        calls.append(site_id)
        return {
            "snapshot_id": "spider-photo",
            "resource_revision_id": "revision-photo",
            "data_type": "synthetic_demo",
            "stale": False,
            "resources": {"stages": [], "limitations": []},
        }

    monkeypatch.setattr(runner, "spider_context", context)
    headers = await login_as(client, "manager")
    off = await client.post(
        "/api/sites/s1/photo-analyses",
        headers=headers,
        files={"image": ("off.jpg", mock_frame("pit-loading"), "image/jpeg")},
        data={"useSpider": "false"},
    )
    on = await client.post(
        "/api/sites/s1/photo-analyses",
        headers=headers,
        files={"image": ("on.jpg", mock_frame("pit-loading"), "image/jpeg")},
        data={"useSpider": "true"},
    )
    assert off.status_code == on.status_code == 201

    async with SessionLocal() as session:
        off_request = await session.scalar(
            select(AnalyticsRequest).where(AnalyticsRequest.snapshot_id == off.json()["id"], AnalyticsRequest.camera_id.is_(None))
        )
        assert off_request is not None and json.loads(off_request.metadata_json)["schema_version"] == "frame-analysis-input-v1"
    assert calls == ["s1"]


async def test_photo_requires_configured_analytics_before_persistence(client, monkeypatch):
    from app.services.camera_client import mock_frame

    monkeypatch.setattr(runner, "_analytics", Analytics())
    headers = await login_as(client, "manager")
    response = await client.post(
        "/api/sites/s1/photo-analyses",
        headers=headers,
        files={"image": ("loading.jpg", mock_frame("pit-loading"), "image/jpeg")},
    )
    assert response.status_code == 503
    async with SessionLocal() as session:
        assert not list(await session.scalars(select(Snapshot).where(Snapshot.source == "photo")))


async def test_photo_persists_terminal_preparation_failure(client, services, monkeypatch):
    async def unavailable():
        raise CatalogError("справочник недоступен")

    monkeypatch.setattr(services.catalog, "get", unavailable)
    headers = await login_as(client, "manager")
    post = await client.post(
        "/api/sites/s1/photo-analyses",
        headers=headers,
        files={"image": ("loading.jpg", mock_frame("pit-loading"), "image/jpeg")},
        data={"useSpider": "true"},
    )
    assert post.status_code == 201, post.text
    body = post.json()
    assert body["answers"] and {answer["state"] for answer in body["answers"]} == {"error"}
    limitation = "Spider был выбран, но успешного импорта нет; дополнительный контекст не передан."
    assert all(answer["limitations"] == [limitation] for answer in body["answers"])
    assert {answer["errorCode"] for answer in body["answers"]} == {"preparation_failure"}

    image = await client.get(body["imageUrl"], headers=headers)
    assert image.status_code == 200 and image.headers["cache-control"] == "private, no-store"
    listed = await client.get("/api/sites/s1/photo-analyses", headers=headers)
    row = next(item for item in listed.json() if item["id"] == body["id"])
    assert {answer["errorCode"] for answer in row["answers"]} == {"preparation_failure"}
    async with SessionLocal() as session:
        snapshot = await session.get(Snapshot, body["id"])
        requests = list(await session.scalars(select(AnalyticsRequest).where(AnalyticsRequest.snapshot_id == body["id"])))
        assert snapshot is not None and snapshot.camera_id is None
        assert requests and {request.camera_id for request in requests} == {None}
        assert all(request.notes == ["spider_context_unavailable"] for request in requests)


async def test_push_photo_analyzer_uses_local_or_explicitly_unsupported(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "analysis_provider", "push")
    monkeypatch.setattr(detector_module, "get_detector", lambda: None)
    unavailable = analysis_module.get_photo_analyzer()
    result = await unavailable.analyze(b"not-used")
    assert unavailable.name == "local"
    assert result.supported is False and result.detections == []

    detector = object()
    selected = object()
    captured = []
    monkeypatch.setattr(detector_module, "get_detector", lambda: detector)
    monkeypatch.setattr(analysis_module, "LocalAnalyzer", lambda value: captured.append(value) or selected)
    assert analysis_module.get_photo_analyzer() is selected
    assert captured == [detector]


def test_photo_spider_no_import_note_survives_pending_error_and_v1_response():
    request = type(
        "Request",
        (),
        {
            "metadata_json": json.dumps({"schema_version": "frame-analysis-input-v1"}),
            "notes": ["spider_context_unavailable"],
            "trigger": "photo",
            "observed_at": None,
        },
    )()
    error = type(
        "Result",
        (),
        {
            "state": "error",
            "finished_at": None,
            "error_code": "preparation_failure",
            "error": "catalog unavailable",
            "result": None,
        },
    )()
    v1_done = type(
        "Result",
        (),
        {
            "state": "done",
            "finished_at": None,
            "outcome": "insufficient_evidence",
            "error_code": None,
            "error": None,
            "result": {
                "schema_version": "frame-analysis-result-v1",
                "versions": {},
                "limitations": [],
                "current_work": {"work_groups": []},
                "transition": {"status": "not_evaluated"},
                "schedule": {"status": "not_evaluated"},
            },
        },
    )()

    pending = work_api._answer("deterministic", request, None, work_api._Names([], None), newer_pending=False, spider_stale=False)
    failed = work_api._answer("deterministic", request, error, work_api._Names([], None), newer_pending=False, spider_stale=False)
    done = work_api._answer("deterministic", request, v1_done, work_api._Names([], None), newer_pending=False, spider_stale=False)

    limitation = "Spider был выбран, но успешного импорта нет; дополнительный контекст не передан."
    assert all(answer.limitations == [limitation] for answer in (pending, failed, done))
    assert all(
        answer.resource_assessment is None and answer.analysis_mode is None and answer.resource_evidence == []
        for answer in (pending, failed, done)
    )


def test_photo_projects_v2_evidence_from_its_own_stored_metadata():
    request = type(
        "Request",
        (),
        {
            "metadata_json": json.dumps(
                {
                    "schema_version": "frame-analysis-input-v2",
                    "resource_plan": {
                        "stages": [
                            {
                                "name": "Бетонирование",
                                "equipment": [{"source_name": "Бетоносмеситель", "planned_quantity": 1}],
                            }
                        ]
                    },
                    "cv": {"detections": [{"class_code": "ConcreteMixer"}]},
                }
            ),
            "notes": [],
            "trigger": "photo",
            "observed_at": None,
        },
    )()
    assessment = {
        "status": "demonstration",
        "reason_codes": ["demonstration_mode"],
        "stage_code": None,
        "selection_basis": None,
        "basis": "cv_detections",
        "coverage": "unknown",
        "equipment_items": [],
        "planned_volume": None,
        "planned_work_shifts": None,
        "planned_productivity": None,
        "actual_volume": None,
        "actual_productivity": None,
        "evidence_refs": ["/resource_plan/stages/0/equipment/0", "/cv/detections/0"],
        "limitations": ["Демонстрационный источник."],
    }
    row = type(
        "Result",
        (),
        {
            "state": "done",
            "finished_at": None,
            "outcome": "insufficient_evidence",
            "error_code": None,
            "error": None,
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
        },
    )()

    answer = work_api._answer("deterministic", request, row, work_api._Names([], None), newer_pending=False, spider_stale=False)

    assert answer.resource_assessment is not None and answer.analysis_mode == "demonstration"
    assert [item.description for item in answer.resource_evidence] == [
        "План: этап «Бетонирование»; позиция «Бетоносмеситель», плановое количество: 1.",
        "Локальное наблюдение: класс «ConcreteMixer», количество: 1.",
    ]
