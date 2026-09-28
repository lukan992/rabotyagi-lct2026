"""Доступ и роли."""

import pytest

from tests.conftest import login_as

pytestmark = pytest.mark.anyio


async def test_password_login(client):
    ok = await client.post("/api/auth/login", json={"login": "Prorab ", "password": "stand-password"})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "foreman" and ok.json()["user"]["siteIds"] == ["s1"]
    bad = await client.post("/api/auth/login", json={"login": "prorab", "password": "wrong"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Неверный логин или пароль"
    assert (await client.get("/api/alerts")).status_code == 401
    assert (await client.get("/api/alerts", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_foreman_sees_only_own_site(client):
    foreman = await login_as(client, "foreman")
    for path in ("sites", "zones", "cameras", "stages", "snapshots", "alerts"):
        rows = (await client.get(f"/api/{path}", headers=foreman)).json()
        site_ids = {r.get("siteId") or r.get("id") for r in rows} if path != "snapshots" else set()
        assert rows and site_ids <= {"s1"}, path
    cameras = {c["id"] for c in (await client.get("/api/cameras", headers=foreman)).json()}
    snapshots = (await client.get("/api/snapshots", headers=foreman)).json()
    assert {s["cameraId"] for s in snapshots} <= cameras
    assert (await client.get("/api/sites/s2/equipment-check", headers=foreman)).status_code == 404
    assert (await client.get("/api/users", headers=foreman)).status_code == 403
    assert (await client.get("/api/audit", headers=foreman)).status_code == 403  # журнал действий — только администратору


async def test_alert_transitions_by_role(client):
    foreman, manager, inspector, admin = [await login_as(client, r) for r in ("foreman", "manager", "inspector", "admin")]
    alerts = (await client.get("/api/alerts?state=open&siteId=s1", headers=manager)).json()
    shortage = next(a for a in alerts if a["kind"] == "missing")
    url = f"/api/alerts/{shortage['id']}/actions"

    assert (await client.post(url, headers=foreman, json={"status": "prescribed"})).status_code == 403
    assert (await client.post(url, headers=foreman, json={"status": "resolved"})).status_code == 409  # сначала нужно ответить

    ack = await client.post(url, headers=foreman, json={"status": "acknowledged", "comment": ""})
    assert ack.json()["status"] == "acknowledged" and ack.json()["history"][-1]["text"].startswith("Техника уже едет")

    prescribed = (
        await client.post(url, headers=inspector, json={"status": "prescribed", "comment": "обеспечить два самосвала"})
    ).json()
    assert prescribed["prescriptionNo"] == "115-П" and "№ 115-П" in prescribed["history"][-1]["text"]  # 114-П уже выдано
    assert (await client.post(url, headers=manager, json={"status": "resolved"})).status_code == 409
    assert (await client.post(url, headers=foreman, json={"status": "false_positive"})).status_code == 409
    closed = await client.post(url, headers=inspector, json={"status": "resolved"})
    assert closed.json()["status"] == "resolved" and closed.json()["isOpen"] is False

    # администратор может всё, что и остальные роли: в том числе выдать и закрыть предписание
    crane = next(a for a in alerts if a["kind"] == "unexpected")
    crane_url = f"/api/alerts/{crane['id']}/actions"
    assert (await client.post(crane_url, headers=admin, json={"status": "prescribed", "comment": ""})).json()[
        "status"
    ] == "prescribed"
    assert (await client.post(crane_url, headers=admin, json={"status": "resolved"})).json()["status"] == "resolved"

    log = (await client.get("/api/audit?action=alert", headers=admin)).json()
    assert log[0]["actorLogin"] == "admin" and log[0]["entityName"] == crane["code"] and "Закрыл" in log[0]["summary"]

    # предписание со сроком: срок сохраняется и попадает в историю, срок в прошлом не принимается
    other = next(a for a in (await client.get("/api/alerts?state=open", headers=inspector)).json() if a["status"] != "prescribed")
    other_url = f"/api/alerts/{other['id']}/actions"
    past = await client.post(other_url, headers=inspector, json={"status": "prescribed", "dueDate": "2020-01-01"})
    assert past.status_code == 422
    due = (await client.post(other_url, headers=inspector, json={"status": "prescribed", "dueDate": "2099-03-05"})).json()
    assert due["prescriptionDue"] == "2099-03-05" and "до 05.03.2099" in due["history"][-1]["text"]


async def test_weekly_report_and_meta(client):
    inspector = await login_as(client, "inspector")
    report = (await client.get("/api/reports/weekly", headers=inspector)).json()
    assert len(report["byDay"]) == 7 and sum(d["count"] for d in report["byDay"]) == report["total"] == 12
    assert report["falsePositive"] == 1 and all(k["key"] != "camera_offline" for k in report["byKind"])
    assert (await client.get("/api/reports/weekly", headers=await login_as(client, "foreman"))).status_code == 403
    meta = (await client.get("/api/meta")).json()
    assert meta["demoMode"] is True and meta["analysisProvider"] == "mock"
