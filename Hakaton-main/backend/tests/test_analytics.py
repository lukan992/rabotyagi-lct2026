"""Сервисы аналитики коллеги (контракт frame-analysis-v1): запрос из нашей базы, вызов обоих сервисов, повторы и
потерянный ответ, хранение и показ ответов, сопоставление работ плана со справочником, журнал наблюдений.

Сервисы — имитация app.mock_analytics, подключённая напрямую (без сокетов). Она проверяет запрос строго по контракту,
поэтому «имитация ответила» здесь значит и «запрос собран по контракту»."""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta

import httpx
import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import func, select

from app import mock_analytics
from app.api.work import RECENT
from app.config import BASE_DIR, get_settings
from app.db import SessionLocal, utcnow
from app.models import AnalyticsRequest, AnalyticsResult, Camera, Observation, Site, Stage
from app.services.analytics import client as client_module
from app.services.analytics import runner
from app.services.analytics.catalog import parse_catalog
from app.services.analytics.client import Reply, ServiceClient
from app.services.analytics.request import build_request
from app.services.analytics.result import check_result
from app.services.analytics.runner import fresh_snapshot
from tests.conftest import check, feed, login_as

pytestmark = pytest.mark.anyio
settings = get_settings()
CATALOG = parse_catalog(mock_analytics.CATALOG)  # справочник сервисов коллеги 0.2.0 — он же у имитации
SCHEMA = json.loads((BASE_DIR / "docs" / "frame-analysis-v1.schema.json").read_text(encoding="utf-8"))


def conforms(name: str, message: dict) -> list[str]:
    """Нарушения JSON Schema контракта (раздел $defs.<name>, с проверкой date-time) — пусто, если всё по схеме."""
    schema = {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{name}"}
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(message)]


@pytest.fixture
def services(monkeypatch):
    """Оба сервиса подключены — это имитация, без задержек."""
    monkeypatch.setattr(settings, "deterministic_service_url", "http://analytics.test/deterministic")
    monkeypatch.setattr(settings, "vlm_llm_service_url", "http://analytics.test/vlm_llm")
    monkeypatch.setitem(mock_analytics.DELAYS_S, "deterministic", 0)
    monkeypatch.setitem(mock_analytics.DELAYS_S, "vlm_llm", 0)
    analytics = runner.Analytics(transport=httpx.ASGITransport(app=mock_analytics.app))
    monkeypatch.setattr(runner, "_analytics", analytics)
    return analytics


async def _pit_frame():  # noqa: ANN202
    """Камера котлована ЖК только что прислала кадр: экскаватор грузит самосвал."""
    await feed("c1", "pit-loading")
    await check("s1")
    async with SessionLocal() as session:
        return await fresh_snapshot(session, "c1", utcnow())


async def _build(request_id: str = "fa_test"):  # noqa: ANN202
    snapshot = await _pit_frame()
    async with SessionLocal() as session:
        site, camera = await session.get(Site, "s1"), await session.get(Camera, "c1")
        snapshot = await session.merge(snapshot)
        return await build_request(session, request_id=request_id, site=site, camera=camera, snapshot=snapshot, catalog=CATALOG)


async def test_request_follows_contract(client):
    built = await _build()
    meta = built.metadata
    assert meta["schema_version"] == "frame-analysis-input-v1" and meta["object_type_code"] == "housing"
    assert meta["catalog_version"] == mock_analytics.CATALOG_VERSION
    # кадр: те же байты, их sha256 и размеры; время — с поясом объекта, оно же — граница истории
    frame = meta["frame"]
    assert frame["image_sha256"] == hashlib.sha256(built.image.data).hexdigest() == built.image.sha256
    assert (frame["width"], frame["height"], frame["media_type"]) == (1280, 720, "image/jpeg")
    assert frame["observed_at"].endswith("+03:00") and meta["history"]["as_of"] == frame["observed_at"]
    assert meta["scope"] == {"plan_stream_code": "main", "roi_bbox": None, "source_ref": "stroykontrol:cameras/c1/zones/z1-pit"}
    # техника — коды справочника и рамки в долях кадра
    cv = meta["cv"]
    assert cv["status"] == "ok" and cv["source_ref"] == "stroykontrol:analysis/mock"
    assert {d["class_code"] for d in cv["detections"]} == {"Excavator", "DumpTruck"}  # коды справочника коллеги
    for d in cv["detections"]:
        x0, y0, x1, y1 = d["bbox"]
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and d["detection_id"].startswith("d")
    # план: только работы, по графику; stage_id — справочника; конец — полночь после даты окончания
    plan = meta["plan"]
    keys = [s["step_key"] for s in plan["steps"]]
    assert keys == ["s1-prep", "s1-excavation", "s1-soil", "s1-found", "s1-backfill", "s1-frame"]
    assert [s["sequence_no"] for s in plan["steps"]] == [1, 2, 3, 4, 5, 6]
    dig = plan["steps"][1]
    async with SessionLocal() as session:
        work = await session.get(Stage, "s1-excavation")
    assert dig["stage_id"] == 47 and dig["planned_start_at"] == f"{work.start_date.isoformat()}T00:00:00+03:00"
    assert dig["planned_end_at"] == f"{(work.end_date + timedelta(days=1)).isoformat()}T00:00:00+03:00"
    assert built.plan_note is None and plan["revision_id"].startswith("rev-")
    # история: без текущего кадра; отметки руководителя — подтверждённый ход без точных дат
    history = meta["history"]
    assert history["complete"] is True and all(o["image_id"] != frame["image_id"] for o in history["observations"])
    assert history["observations"], "журнал наблюдений демо-данных пуст"
    events = {e["step_key"]: e for e in history["progress_events"]}
    assert events["s1-prep"]["state"] == "completed" and events["s1-excavation"]["state"] == "in_progress"
    assert all(e["actual_started_at"] is None and e["actual_completed_at"] is None for e in events.values())
    assert all(e["plan_revision_id"] == plan["revision_id"] for e in events.values())
    # отпечаток входа: metadata как отправлена + 0x00 + кадр
    assert built.input_sha256 == hashlib.sha256(built.body + b"\x00" + built.image.data).hexdigest()
    assert json.loads(built.body) == meta
    # схема контракта коллеги и строгая проверка имитации — без замечаний
    assert conforms("request", meta) == []
    mock_analytics.validate(meta, built.image.data, "image/jpeg", {})


async def test_plan_revision_follows_plan_content(client):
    first, again = await _build("fa_1"), await _build("fa_2")
    assert first.plan_revision_id == again.plan_revision_id  # план тот же — версия та же
    async with SessionLocal() as session:
        work = await session.get(Stage, "s1-frame")
        work.end_date += timedelta(days=7)
        await session.commit()
    assert (await _build("fa_3")).plan_revision_id != first.plan_revision_id


async def test_plan_is_not_sent_without_catalog_work(client):
    async with SessionLocal() as session:
        work = await session.get(Stage, "s1-backfill")
        work.catalog_stage_id = None
        await session.commit()
    built = await _build()
    assert built.metadata["plan"] is None and built.metadata["history"]["progress_events"] == []
    assert "не выбран вид работ по справочнику" in built.plan_note and "Обратная засыпка" in built.plan_note

    async with SessionLocal() as session:  # сопоставлено по старой версии справочника — тоже не отправляем
        work = await session.get(Stage, "s1-backfill")
        work.catalog_stage_id, work.catalog_version = 123, "old-version"
        await session.commit()
    built = await _build("fa_2")
    assert built.metadata["plan"] is None and "обновился до версии" in built.plan_note


async def test_work_without_equipment_is_planned_for_deadlines_only(client, services):
    """no_class (без техники: геодезия, отселение) — пункт плана для сроков и отметок; по кадру его не называют."""
    async with SessionLocal() as session:
        work = await session.get(Stage, "s1-soil")
        work.catalog_stage_id = 39  # «Закупка оборудования» — работа без техники
        await session.commit()
    built = await _build()
    steps = {s["step_key"]: s["stage_id"] for s in built.metadata["plan"]["steps"]}
    assert built.plan_note is None and steps["s1-soil"] == 39
    mock_analytics.validate(built.metadata, built.image.data, "image/jpeg", {})  # сервис такой пункт принимает
    answer = mock_analytics.answer("deterministic", built.metadata, built.input_sha256)
    candidates = [c["step_key"] for g in answer["current_work"]["work_groups"] for c in g["candidates"]]
    assert "s1-soil" not in candidates and candidates[0] == "s1-excavation"  # работу без техники по кадру не называют
    assert "s1-soil" in {i["step_key"] for i in answer["schedule"]["items"]}  # сроки по ней проверяются
    assert answer["transition"]["status"] == "not_distinguishable_by_equipment"  # у следующей работы нет техники


async def test_equipment_missing_in_catalog_is_left_out_with_note(client):
    payload = json.loads(json.dumps(mock_analytics.CATALOG))
    payload["equipment_classes"] = [c for c in payload["equipment_classes"] if c["code"] != "DumpTruck"]
    snapshot = await _pit_frame()
    async with SessionLocal() as session:
        built = await build_request(
            session, request_id="fa_x", site=await session.get(Site, "s1"), camera=await session.get(Camera, "c1"),
            snapshot=await session.merge(snapshot), catalog=parse_catalog(payload),
        )  # fmt: skip
    assert {d["class_code"] for d in built.metadata["cv"]["detections"]} == {"Excavator"}
    assert any("dump_truck" in note for note in built.notes)


async def test_both_services_answer_and_are_shown_side_by_side(client, services):
    await _pit_frame()
    requests = await services.analyze_camera("c1", trigger="manual", only_new=False)
    results = {request.results[0].service: request.results[0] for request in requests}
    assert {s: r.state for s, r in results.items()} == {"deterministic": "done", "vlm_llm": "done"}
    assert results["deterministic"].outcome == "assessed" and results["deterministic"].analysis_id.startswith("an-deterministic")
    deterministic = next(request for request in requests if request.results[0].service == "deterministic")
    assert deterministic.plan_revision_id and deterministic.metadata_json and deterministic.catalog_version == mock_analytics.CATALOG_VERSION

    manager = await login_as(client, "manager")
    work = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()
    assert work["enabled"] and work["canRun"] and work["services"] == ["deterministic", "vlm_llm"] and not work["planIssue"]
    camera = next(c for c in work["cameras"] if c["cameraId"] == "c1")
    assert [c["cameraId"] for c in work["cameras"]] == ["c1"]  # въезд и склад сервисам не отправляются
    by_service = {a["service"]: a for a in camera["answers"]}
    rules, vision = by_service["deterministic"], by_service["vlm_llm"]
    # как у сервиса коллеги: одна группа — все работы плана, совместимые с техникой на кадре, по убыванию оценки
    group = rules["groups"][0]
    names = [w["name"] for w in group["works"]]
    assert group["match"] == "ambiguous" and names == [
        "Разработка котлована",
        "Вывоз грунта",
        "Обратная засыпка",
        "Подготовка площадки",
    ]
    assert group["evidence"] and vision["groups"][0]["visualState"] == "operation_indicated"
    assert camera["matchesPlan"] is True  # котлован по графику сейчас и копают
    assert rules["transition"]["status"] == "not_distinguishable_by_equipment"  # следующая работа — та же техника
    assert rules["transition"]["current"]["name"] == "Разработка котлована"
    assert rules["schedule"]["status"] == "insufficient_evidence" and vision["schedule"] is None
    assert camera["imageUrl"].startswith("/media/frames/c1/") and rules["model"].startswith("mock-0.2.0")


async def test_possible_delay_on_the_road(client, services):
    await feed("c7", "road-dumptruck")
    await check("s3")
    requests = await services.analyze_camera("c7", trigger="manual")
    rules = next(request.results[0] for request in requests if request.results[0].service == "deterministic").result
    late = next(i for i in rules["schedule"]["items"] if i["step_key"] == "s3-base")
    # основание дороги по отметке руководителя ещё не закончено, а срок прошёл
    assert (late["status"], late["reason_code"]) == ("possible_delay", "open_state_after_deadline") and late[
        "overdue_seconds"
    ] > 0
    assert rules["schedule"]["status"] == "possible_delay"
    group = rules["current_work"]["work_groups"][0]
    assert [c["step_key"] for c in group["candidates"]] == ["s3-base", "s3-asphalt", "s3-prep"]  # самосвал — у всех трёх


async def test_service_failure_is_kept_apart(client, services, monkeypatch):
    monkeypatch.setitem(mock_analytics.FAULTS, "vlm_llm", "model_failure")
    await _pit_frame()
    requests = await services.analyze_camera("c1", trigger="manual", only_new=False)
    results = {request.results[0].service: request.results[0] for request in requests}
    assert results["deterministic"].state == "done"  # отказ одного не мешает ответу другого
    failed = results["vlm_llm"]
    assert (failed.state, failed.http_status, failed.error_code, failed.result) == ("error", 502, "model_failure", None)
    manager = await login_as(client, "manager")
    camera = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()["cameras"][0]
    vision = next(a for a in camera["answers"] if a["service"] == "vlm_llm")
    assert vision["state"] == "error" and vision["errorCode"] == "model_failure" and vision["groups"] == []
    assert "имитация отказа" in vision["error"]
    foreman = await login_as(client, "foreman")  # прорабу — суть без технических подробностей
    camera = (await client.get("/api/sites/s1/work-analysis", headers=foreman)).json()["cameras"][0]
    vision = next(a for a in camera["answers"] if a["service"] == "vlm_llm")
    assert vision["state"] == "error" and vision["error"] is None


async def test_vlm_requests_are_globally_limited_and_waiting_is_visible(client, services, monkeypatch):
    built = await _build()
    active = maximum = 0
    three_started, release = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):  # noqa: ANN003
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 3:
            three_started.set()
        try:
            await release.wait()
        finally:
            active -= 1
        return Reply("error", code="test")

    monkeypatch.setattr(services.clients["vlm_llm"], "analyze", delayed)
    first = [asyncio.create_task(services._ask("vlm_llm", built)) for _ in range(3)]
    await asyncio.wait_for(three_started.wait(), timeout=1)
    fourth = asyncio.create_task(services._ask("vlm_llm", built))
    await asyncio.sleep(0)
    assert maximum == 3 and services.llm_waiting == 1

    cancelled = asyncio.create_task(services._ask("vlm_llm", built))
    await asyncio.sleep(0)
    assert services.llm_waiting == 2
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert services.llm_waiting == 1

    manager = await login_as(client, "manager")
    waiting = await client.get("/api/sites/s1/work-analysis", headers=manager)
    assert waiting.status_code == 200 and waiting.json()["llmWaiting"] == 1

    release.set()
    replies = await asyncio.gather(*first, fourth)
    assert len(replies) == 4 and maximum == 3 and services.llm_waiting == 0


async def test_repeat_of_the_same_request_returns_the_same_analysis(client, services):
    built = await _build()
    service = services.clients["deterministic"]
    kwargs = {
        "request_id": built.request_id,
        "site_id": "s1",
        "schema_version": "frame-analysis-input-v1",
        "metadata": built.body,
        "image": built.image.data,
        "media_type": "image/jpeg",
    }
    first, again = await service.analyze(**kwargs), await service.analyze(**kwargs)
    assert first.state == again.state == "done" and first.result["analysis_id"] == again.result["analysis_id"]
    changed = await service.analyze(**{**kwargs, "metadata": built.body.replace(b'"main"', b'"main-2"')})
    assert (changed.state, changed.code) == ("error", "idempotency_conflict")  # тот же request_id с другим входом


def _answer(request: httpx.Request) -> dict:
    """Ответ «как у сервиса» на запрос нашего клиента: context — из его metadata."""
    body = request.content
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    parts = {}
    for part in body.split(b"--" + boundary)[1:-1]:
        head, _, content = part.strip(b"\r\n").partition(b"\r\n\r\n")
        parts[head.split(b'name="')[1].split(b'"')[0].decode()] = content
    meta = json.loads(parts["metadata"])
    return mock_analytics.answer("deterministic", meta, hashlib.sha256(parts["metadata"] + b"\x00" + parts["image"]).hexdigest())


async def test_client_retries_only_retryable_refusals(client, monkeypatch):
    monkeypatch.setattr(client_module, "RETRY_DELAYS_S", (0, 0))
    built = await _build()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["idempotency-key"])
        if len(calls) < 3:
            error = {"schema_version": "frame-analysis-error-v1", "request_id": built.request_id, "service": "deterministic",
                     "code": "busy", "message": "занят", "details": [], "retryable": True, "execution_state": "not_started"}  # fmt: skip
            return httpx.Response(429, json=error, headers={"Retry-After": "0"})
        return httpx.Response(200, json=_answer(request))

    service = ServiceClient("deterministic", "http://d.test", "token", 30, transport=httpx.MockTransport(handler))
    reply = await service.analyze(
        request_id=built.request_id, site_id="s1", schema_version="frame-analysis-input-v1", metadata=built.body,
        image=built.image.data, media_type="image/jpeg"
    )
    assert reply.state == "done" and calls == [built.request_id] * 3  # два повтора с тем же ключом
    assert check_result(reply.result, service="deterministic", metadata=built.metadata, input_sha256=built.input_sha256) is None

    calls.clear()

    def invalid(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        error = {"schema_version": "frame-analysis-error-v1", "request_id": built.request_id, "service": "deterministic",
                 "code": "unknown_class", "message": "нет класса", "retryable": False, "execution_state": "not_started",
                 "details": [{"path": "/cv/detections/0/class_code", "code": "unknown_class", "message": "нет в справочнике"}]}  # fmt: skip
        return httpx.Response(422, json=error)

    service = ServiceClient("deterministic", "http://d.test", None, 30, transport=httpx.MockTransport(invalid))
    reply = await service.analyze(
        request_id=built.request_id, site_id="s1", schema_version="frame-analysis-input-v1", metadata=built.body,
        image=built.image.data, media_type="image/jpeg"
    )
    assert (reply.state, reply.code, len(calls)) == ("error", "unknown_class", 1)  # неповторяемое — без повторов
    assert "/cv/detections/0/class_code" in reply.message


async def test_lost_response_is_looked_up_not_repeated(client):
    built = await _build()
    posts, lookups = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(request)
            raise httpx.ReadTimeout("нет ответа", request=request)
        lookups.append(str(request.url))
        return httpx.Response(200, json=_answer(posts[0]))

    service = ServiceClient("deterministic", "http://d.test", None, 30, transport=httpx.MockTransport(handler))
    reply = await service.analyze(
        request_id=built.request_id, site_id="s1", schema_version="frame-analysis-input-v1", metadata=built.body,
        image=built.image.data, media_type="image/jpeg"
    )
    assert reply.state == "done" and len(posts) == 1  # анализ заново не запускали — узнали результат
    assert lookups == [f"http://d.test/v1/analyses/by-request/{built.request_id}?site_id=s1"]

    def not_found(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise httpx.RemoteProtocolError("оборвалось", request=request)
        return httpx.Response(404, json={"code": "analysis_not_found", "message": "нет", "retryable": False})

    service = ServiceClient("deterministic", "http://d.test", None, 30, transport=httpx.MockTransport(not_found))
    reply = await service.analyze(
        request_id=built.request_id, site_id="s1", schema_version="frame-analysis-input-v1", metadata=built.body,
        image=built.image.data, media_type="image/jpeg"
    )
    assert reply.state == "unknown" and "неизвестно" in reply.message  # повторять автоматически нельзя


async def test_answer_about_another_frame_is_rejected(client):
    built = await _build()
    result = mock_analytics.answer("deterministic", built.metadata, built.input_sha256)
    assert check_result(result, service="deterministic", metadata=built.metadata, input_sha256=built.input_sha256) is None
    wrong = json.loads(json.dumps(result))
    wrong["context"]["image_sha256"] = "0" * 64
    assert "не к этому кадру" in check_result(
        wrong, service="deterministic", metadata=built.metadata, input_sha256=built.input_sha256
    )
    wrong = json.loads(json.dumps(result))
    wrong["current_work"]["work_groups"][0]["candidates"][0]["stage_id"] = 999
    assert "не пункт отправленного плана" in check_result(
        wrong, service="deterministic", metadata=built.metadata, input_sha256=built.input_sha256
    )
    assert "подписан сервисом" in check_result(
        result, service="vlm_llm", metadata=built.metadata, input_sha256=built.input_sha256
    )


async def test_catalog_work_is_chosen_by_hand_and_checked(client, services):
    manager = await login_as(client, "manager")
    catalog = (await client.get("/api/analytics/catalog?siteId=s3", headers=manager)).json()
    assert catalog["enabled"] and catalog["version"] == mock_analytics.CATALOG_VERSION and catalog["objectType"] == "roads"
    ids = {w["stageId"] for w in catalog["works"]}
    # 149 «Устройство нижнего слоя покрытия» — для дорог; 182 «Устройство ж/б конструкций» (каркас здания) — нет.
    # Одноимённая 104 (опоры) для дорог есть: названия в справочнике повторяются, поэтому список — по разделам
    assert 149 in ids and 182 not in ids and 104 in ids
    assert all(w["kind"] in ("concrete", "no_class") for w in catalog["works"])  # сводные этапы не выбираются
    purchase = next(w for w in catalog["works"] if w["stageId"] == 39)  # работа без техники — выбирается, с пометкой
    assert purchase["kind"] == "no_class" and purchase["name"] == "Закупка оборудования"
    assert purchase["path"][0] == "Подготовка территории"  # stage_path у коллеги — строка «Раздел / … / Работа»

    def body(**extra) -> dict:  # noqa: ANN003
        return {
            "name": "Обратная засыпка",
            "level": 2,
            "parentId": "s1-l1-found",
            "start": "2026-11-09",
            "end": "2026-11-27",
            **extra,
        }

    changed = await client.patch("/api/stages/s1-backfill", headers=manager, json=body(catalogStageId=72))
    assert changed.status_code == 200 and changed.json()["catalogStageId"] == 72
    assert changed.json()["catalogVersion"] == mock_analytics.CATALOG_VERSION
    summary = await client.patch("/api/stages/s1-backfill", headers=manager, json=body(catalogStageId=27))
    assert summary.status_code == 422 and "сводный этап" in summary.json()["detail"]
    roads_only = await client.patch("/api/stages/s1-backfill", headers=manager, json=body(catalogStageId=80))
    assert roads_only.status_code == 422 and "не относится" in roads_only.json()["detail"]
    kept = await client.patch("/api/stages/s1-backfill", headers=manager, json=body())  # поля нет — вид работ не трогаем
    assert kept.json()["catalogStageId"] == 72
    cleared = await client.patch("/api/stages/s1-backfill", headers=manager, json=body(catalogStageId=None))
    assert cleared.json()["catalogStageId"] is None and cleared.json()["catalogVersion"] is None
    no_class = await client.patch("/api/stages/s1-backfill", headers=manager, json=body(catalogStageId=39))
    assert no_class.status_code == 200 and no_class.json()["catalogStageId"] == 39  # работа без техники — можно

    foreman = await login_as(client, "foreman")
    assert (await client.get("/api/analytics/catalog", headers=foreman)).status_code == 403


async def test_catalog_work_needs_connected_services(client):
    manager = await login_as(client, "manager")
    assert (await client.get("/api/analytics/catalog", headers=manager)).json() == {
        "enabled": False, "version": None, "objectType": None, "works": [], "error": None,
    }  # fmt: skip
    body = {"name": "Обратная засыпка", "level": 2, "parentId": "s1-l1-found", "start": "2026-11-09", "end": "2026-11-27"}
    refused = await client.patch("/api/stages/s1-backfill", headers=manager, json={**body, "catalogStageId": 72})
    assert refused.status_code == 503 and "недоступен" in refused.json()["detail"]
    kept = await client.patch("/api/stages/s1-backfill", headers=manager, json={**body, "catalogStageId": 77})
    assert kept.status_code == 200  # прежний вид работ сохранить можно и без сервисов — даты менять не мешает
    work = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()
    assert work["enabled"] is False and not work["canRun"] and all(c["answers"] == [] for c in work["cameras"])


async def test_manual_run_and_schedule(client, services):
    manager, foreman = await login_as(client, "manager"), await login_as(client, "foreman")
    assert (await client.post("/api/sites/s1/work-analysis", headers=foreman)).status_code == 403
    stale = await client.post("/api/sites/s1/work-analysis", headers=manager)
    assert stale.status_code == 409 and "свежих кадров" in stale.json()["detail"]  # демо-кадры 10-минутной давности

    await _pit_frame()
    started = await client.post("/api/sites/s1/work-analysis", headers=manager)
    assert started.status_code == 202 and started.json()["running"] is True
    await asyncio.gather(*services._manual.values())
    work = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()
    assert not work["running"] and {a["state"] for a in work["cameras"][0]["answers"]} == {"done"}
    assert work["nextAt"]

    # расписание: кадр c1 уже отправлен — не повторяет; новые кадры других объектов — отправляет по одному разу на сервис
    await feed("c8", "yard-bulldozer")
    await check("s4")
    await services.round()
    await services.round()
    async with SessionLocal() as session:
        sent = list(await session.scalars(select(AnalyticsRequest.camera_id).order_by(AnalyticsRequest.at)))
    assert sent == ["c1", "c1", "c8", "c8"]


async def test_schedule_respects_working_hours_and_model_interval(client, services, monkeypatch):
    """По расписанию: вне рабочего времени кадры не уходят; «по снимку» (платный) — реже, чем «по технике»."""
    monkeypatch.setattr(settings, "analytics_interval_min", 20)
    monkeypatch.setattr(settings, "analytics_vlm_interval_min", 60)
    await _pit_frame()
    monkeypatch.setattr(runner, "working_now", lambda site, at: site.id != "s1")  # на ЖК сейчас нерабочее время
    await services.round()
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(AnalyticsRequest)) == 0

    monkeypatch.setattr(runner, "working_now", lambda site, at: True)
    await services.round()  # первая отправка — обоим сервисам
    async with SessionLocal() as session:
        first = {
            row.results[0].service: row
            for row in await session.scalars(select(AnalyticsRequest).where(AnalyticsRequest.camera_id == "c1"))
        }
        assert set(first) == {"deterministic", "vlm_llm"}
        first["deterministic"].at -= timedelta(minutes=30)  # «по технике» пора, «по снимку» — ещё нет
        await session.commit()
    await _pit_frame()  # свежий кадр
    await services.round()
    async with SessionLocal() as session:
        latest = await session.scalar(
            select(AnalyticsRequest)
            .join(AnalyticsResult)
            .where(AnalyticsRequest.camera_id == "c1", AnalyticsResult.service == "deterministic")
            .order_by(AnalyticsRequest.at.desc())
            .limit(1)
        )
    assert latest.id != first["deterministic"].id and [r.service for r in latest.results] == ["deterministic"]


async def test_observations_are_logged_every_20_minutes(client):
    async def logged() -> list[Observation]:
        async with SessionLocal() as session:
            return list(
                await session.scalars(select(Observation).where(Observation.camera_id == "c1").order_by(Observation.observed_at))
            )

    before = await logged()
    now = utcnow()  # сид записал кадр c1 в журнал 10 минут назад
    for minutes in (0, 15, 20, 36):  # 10 минут с прошлой записи — рано; 25 — пора; 5 — рано; 21 — пора
        await feed("c1", "pit-loading", at=now + timedelta(minutes=minutes))
        await check("s1", at=now + timedelta(minutes=minutes))
    rows = await logged()
    assert len(rows) - len(before) == 2
    fresh = rows[-1]
    assert fresh.zone_kind == "work" and fresh.analyzed and {d["type"] for d in fresh.detections} == {"excavator", "dump_truck"}
    assert len(fresh.image_sha256) == 64 and fresh.provider == "mock"


async def test_cleanup_keeps_latest_metadata_per_service_and_work_keeps_vlm(client, services):
    await _pit_frame()
    first = await services.analyze_camera("c1", trigger="manual", only_new=False)
    followups = [
        await services.analyze_camera("c1", trigger="manual", only_new=False, services=["deterministic"])
        for _ in range(RECENT + 1)
    ]
    async with SessionLocal() as session:
        await runner.cleanup(session)
        old = {request.results[0].service: await session.get(AnalyticsRequest, request.id) for request in first}
        latest = await session.get(AnalyticsRequest, followups[-1][0].id)
        assert old["deterministic"].metadata_json is None and old["deterministic"].input_sha256
        assert old["vlm_llm"].metadata_json and latest.metadata_json
        assert await session.scalar(select(func.count()).select_from(AnalyticsResult)) == RECENT + 3
    manager = await login_as(client, "manager")
    camera = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()["cameras"][0]
    assert {answer["service"] for answer in camera["answers"]} == {"deterministic", "vlm_llm"}



async def test_work_does_not_attribute_different_frames_to_one_image(client, services):
    await _pit_frame()
    paired = await services.analyze_camera("c1", trigger="manual", only_new=False)
    await _pit_frame()
    newer = await services.analyze_camera("c1", trigger="manual", only_new=False, services=["deterministic"])
    assert newer[0].snapshot_id != paired[0].snapshot_id

    manager = await login_as(client, "manager")
    camera = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()["cameras"][0]
    assert {answer["service"] for answer in camera["answers"]} == {"deterministic", "vlm_llm"}
    assert camera["imageUrl"] is None
    assert {datetime.fromisoformat(answer["observedAt"]) for answer in camera["answers"]} == {
        paired[0].observed_at,
        newer[0].observed_at,
    }

async def test_pending_answers_after_restart_become_unknown(client, services):
    await _pit_frame()
    requests = await services.analyze_camera("c1", trigger="manual", only_new=False)
    async with SessionLocal() as session:
        request = requests[0]
        row = await session.get(AnalyticsResult, request.results[0].id)
        row.state = "pending"
        await session.commit()
        await runner.interrupt_pending(session)
        row = await session.get(AnalyticsResult, request.results[0].id, populate_existing=True)
    assert (row.state, row.error_code) == ("unknown", "interrupted")


async def test_mock_answers_follow_the_contract_schema(client, services):
    """Имитация отвечает по той же JSON Schema, что и сервисы коллеги: ответы, отказы, справочник, возможности."""
    built = await _build()
    for service in ("deterministic", "vlm_llm"):
        assert conforms("result", mock_analytics.answer(service, built.metadata, built.input_sha256)) == []
    refusal = mock_analytics._error(
        "deterministic", mock_analytics.Problem(422, "unknown_stage", "сводный этап", "/plan/steps/0"), "fa_x"
    )
    assert conforms("error", json.loads(refusal.body)) == []
    assert conforms("catalog", mock_analytics.CATALOG) == []
    assert conforms("capabilities", await services.clients["vlm_llm"].get_json("/v1/capabilities")) == []


async def test_real_service_answer_is_shown(client, services):
    """Ответ настоящего сервиса коллеги (записан в его комплекте 0.2.0) читается и показывается: названия работ, которых
    нет в нашем плане, берутся из справочника, сроки и следующая работа — как есть."""
    real = json.loads(
        (BASE_DIR / "tests" / "fixtures" / "analytics" / "real-deterministic-mixed.json").read_text(encoding="utf-8")
    )
    assert conforms("result", real) == []
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            AnalyticsRequest(
                id="fa_real",
                site_id="s1",
                camera_id="c1",
                at=now,
                trigger="manual",
                observed_at=now,
                image_sha256="0" * 64,
                input_sha256=real["context"]["input_sha256"],
                catalog_version=real["context"]["catalog_version"],
                plan_revision_id=real["context"]["plan_revision_id"],
                notes=[],
                results=[
                    AnalyticsResult(service="deterministic", state="done", finished_at=now, result=real, outcome="assessed")
                ],
            )  # fmt: skip
        )
        await session.commit()
    services.catalog._catalog = CATALOG  # справочник уже получен
    manager = await login_as(client, "manager")
    camera = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()["cameras"][0]
    rules = next(a for a in camera["answers"] if a["service"] == "deterministic")
    works = rules["groups"][0]["works"]
    assert rules["state"] == "done" and works[0] == {
        "stepKey": "demo-work-a",
        "stageId": 47,
        "name": "Устройство котлована",
        "inPlan": False,
    }
    assert rules["schedule"]["status"] == real["schedule"]["status"] and rules["model"].startswith("0.2.0")
    assert rules["transition"]["status"] == real["transition"]["status"]
    # отставание в секундах у сервиса коллеги дробное — не теряется
    late = next(i for i in real["schedule"]["items"] if i["status"] == "possible_delay")
    shown = next(i for i in rules["schedule"]["items"] if i["status"] == "possible_delay")
    assert shown["overdueS"] == round(late["overdue_seconds"]) and shown["overdueS"] > 0


async def test_real_model_answer_names_works_by_plan(client, services):
    """Настоящий ответ «по снимку» (qwen3.8-27b через gateway, 26.09, кадр котлована демо-плана): модель пишет наши id
    работ — людям показываем названия из плана; версия модели — один раз."""
    real = json.loads((BASE_DIR / "tests" / "fixtures" / "analytics" / "real-vlm-pit.json").read_text(encoding="utf-8"))
    assert conforms("result", real) == [] and "s1-excavation" in real["current_work"]["work_groups"][0]["explanation"]
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            AnalyticsRequest(
                id="fa_vlm",
                site_id="s1",
                camera_id="c1",
                at=now,
                trigger="manual",
                observed_at=now,
                image_sha256="0" * 64,
                input_sha256=real["context"]["input_sha256"],
                catalog_version=real["context"]["catalog_version"],
                notes=[],
                results=[AnalyticsResult(service="vlm_llm", state="done", finished_at=now, result=real, outcome="assessed")],
            )  # fmt: skip
        )
        await session.commit()
    manager = await login_as(client, "manager")
    camera = (await client.get("/api/sites/s1/work-analysis", headers=manager)).json()["cameras"][0]
    vision = next(a for a in camera["answers"] if a["service"] == "vlm_llm")
    group = vision["groups"][0]
    assert [w["name"] for w in group["works"]] == ["Разработка котлована", "Вывоз грунта"] and group[
        "visualState"
    ] == "operation_indicated"
    shown = " ".join([group["explanation"], *(e["explanation"] for e in group["evidence"]), *vision["limitations"]])
    assert "s1-excavation" not in shown and "s1-soil" not in shown and "«Разработка котлована»" in shown
    assert vision["model"] == "0.2.0, qwen3.8-27b"


def test_contract_environment_names(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("DETERMINISTIC_SERVICE_URL", "http://analytics-deterministic:8000/")
    monkeypatch.setenv("VLM_LLM_SERVICE_URL", "http://analytics-vlm:8000")
    monkeypatch.setenv("ANALYTICS_SERVICE_TOKEN", "secret")
    monkeypatch.setenv("ANALYTICS_HTTP_TIMEOUT_SECONDS", "300")
    loaded = Settings()
    assert loaded.analytics_services == {
        "deterministic": "http://analytics-deterministic:8000",
        "vlm_llm": "http://analytics-vlm:8000",
    }
    assert (loaded.analytics_service_token, loaded.analytics_http_timeout_seconds) == ("secret", 300)
    monkeypatch.setenv("DETERMINISTIC_SERVICE_URL", "http://analytics:8000/v1/analyze/frame")
    with pytest.raises(ValueError, match="без пути метода"):
        Settings()
