"""Администратор может всё: объекты, план, зоны, сотрудники и пароли — и всё это видно в журнале действий."""

import json

import httpx
import pytest

from tests.conftest import login_as

pytestmark = pytest.mark.anyio


async def _log(client, admin, action: str) -> list[dict]:  # noqa: ANN001
    return (await client.get(f"/api/audit?action={action}", headers=admin)).json()


async def test_site_plan_and_zones_lifecycle(client):
    admin = await login_as(client, "admin")
    created = await client.post(
        "/api/sites",
        headers=admin,
        json={"name": "Склад ГСМ", "address": "ул. Ленина, 1", "contractor": "ООО «Тест»", "foremanId": "u4"},
    )
    assert created.status_code == 201, created.text
    site = created.json()
    assert site["foreman"] == "Смирнова Ольга"
    users = {u["id"]: u for u in (await client.get("/api/users", headers=admin)).json()}
    assert site["id"] in users["u4"]["siteIds"]  # прораб получил доступ к объекту

    zone = (await client.post(f"/api/sites/{site['id']}/zones", headers=admin, json={"name": "Площадка", "kind": "work"})).json()
    phase = await client.post(
        f"/api/sites/{site['id']}/stages",
        headers=admin,
        json={"name": "Подготовка", "level": 1, "start": "2026-09-01", "end": "2026-12-01"},
    )
    assert phase.status_code == 201
    work = {
        "name": "Подготовка площадки",
        "level": 2,
        "parentId": phase.json()["id"],
        "start": "2026-09-01",
        "end": "2026-12-01",
        "ruleKey": "site_prep",
    }
    assert (await client.post(f"/api/sites/{site['id']}/stages", headers=admin, json=work)).status_code == 201
    orphan = {**work, "parentId": None}
    assert (await client.post(f"/api/sites/{site['id']}/stages", headers=admin, json=orphan)).status_code == 422
    backwards = {**work, "start": "2026-12-02"}
    assert (await client.post(f"/api/sites/{site['id']}/stages", headers=admin, json=backwards)).status_code == 422

    renamed = await client.patch(f"/api/sites/{site['id']}", headers=admin, json={"name": "Склад ГСМ-2", "foremanId": "u4"})
    assert renamed.json()["name"] == "Склад ГСМ-2"
    assert (await client.delete(f"/api/zones/{zone['id']}", headers=admin)).status_code == 204  # пустая зона удаляется
    assert (await client.delete(f"/api/stages/{phase.json()['id']}", headers=admin)).status_code == 204  # вместе с работами
    stages = (await client.get(f"/api/stages?siteId={site['id']}", headers=admin)).json()
    assert stages == []

    actions = [e["action"] for e in (await client.get("/api/audit", headers=admin)).json()][:7]
    assert actions == ["stage.delete", "zone.delete", "site.update", "stage.create", "stage.create", "zone.create", "site.create"]


async def test_deleting_site_removes_its_cameras_and_alerts(client):
    admin = await login_as(client, "admin")
    assert (await client.delete("/api/zones/z1-pit", headers=admin)).status_code == 409  # в зоне камера
    assert (await client.delete("/api/sites/s1", headers=admin)).status_code == 204
    assert "s1" not in {s["id"] for s in (await client.get("/api/sites", headers=admin)).json()}
    assert not [c for c in (await client.get("/api/cameras", headers=admin)).json() if c["siteId"] == "s1"]
    assert not [a for a in (await client.get("/api/alerts", headers=admin)).json() if a["siteId"] == "s1"]
    entry = (await _log(client, admin, "site.delete"))[0]
    assert "ЖК «Северный парк»" in entry["summary"] and "камерами (3)" in entry["summary"]


async def test_who_manages_sites_and_staff(client):
    manager = await login_as(client, "manager")
    created = await client.post("/api/sites", headers=manager, json={"name": "Объект руководителя", "foremanId": "u4"})
    assert created.status_code == 201, created.text  # объекты заводит и руководитель проекта
    site_id = created.json()["id"]
    assert (await client.post(f"/api/sites/{site_id}/zones", headers=manager, json={"name": "Котлован"})).status_code == 201
    phase = {"name": "Земляные работы", "level": 1, "start": "2026-10-01", "end": "2026-10-31"}
    assert (await client.post(f"/api/sites/{site_id}/stages", headers=manager, json=phase)).status_code == 201
    assert (await client.get("/api/users", headers=manager)).status_code == 200  # список нужен, чтобы выбрать прораба

    def foreman_of(users: list[dict]) -> list[str]:
        return [u["id"] for u in users if site_id in u["siteIds"]]

    # без поля foremanId прораб остаётся, с foremanId: null — снимается с объекта
    await client.patch(f"/api/sites/{site_id}", headers=manager, json={"name": "Объект руководителя, корпус 2"})
    assert foreman_of((await client.get("/api/users", headers=manager)).json()) == ["u4"]
    cleared = await client.patch(
        f"/api/sites/{site_id}", headers=manager, json={"name": "Объект руководителя", "foremanId": None}
    )
    assert cleared.status_code == 200 and cleared.json()["foreman"] == ""
    assert foreman_of((await client.get("/api/users", headers=manager)).json()) == []
    # удалить объект и управлять сотрудниками — только администратор
    assert (await client.delete(f"/api/sites/{site_id}", headers=manager)).status_code == 403
    assert (await client.post("/api/users/u1/password", headers=manager, json={"password": "secret-1"})).status_code == 403
    assert (await client.post("/api/users", headers=manager, json={})).status_code == 403
    for role in ("foreman", "inspector"):
        auth = await login_as(client, role)
        assert (await client.post("/api/sites", headers=auth, json={"name": "Объект"})).status_code == 403
        assert (await client.post(f"/api/sites/{site_id}/stages", headers=auth, json=phase)).status_code == 403


async def test_fact_progress_of_stages(client):
    """По графику — сколько должно быть сделано к сегодняшнему дню; по факту — что отметил руководитель (прораб не отмечает)."""
    foreman = await login_as(client, "foreman")
    stages = {s["id"]: s for s in (await client.get("/api/stages?siteId=s1", headers=foreman)).json()}
    digging = stages["s1-excavation"]  # идёт сейчас, ЖК отстаёт от графика
    assert 0 < digging["factProgress"] < digging["planProgress"] < 100 and digging["factUpdatedAt"]
    assert stages["s1-prep"]["factProgress"] == stages["s1-prep"]["planProgress"] == 100  # завершённая работа
    assert stages["s1-frame"]["factProgress"] == stages["s1-frame"]["planProgress"] == 0  # впереди

    # прораб видит выполнение, но не отмечает его — даже на своём объекте
    assert (
        await client.patch("/api/stages/s1-excavation/progress", headers=foreman, json={"factProgress": 80})
    ).status_code == 403
    manager = await login_as(client, "manager")
    marked = await client.patch("/api/stages/s1-excavation/progress", headers=manager, json={"factProgress": 80})
    assert marked.status_code == 200 and marked.json()["factProgress"] == 80
    assert (
        await client.patch("/api/stages/s1-excavation/progress", headers=manager, json={"factProgress": 101})
    ).status_code == 422
    inspector = await login_as(client, "inspector")
    assert (await client.patch("/api/stages/s1-soil/progress", headers=inspector, json={"factProgress": 10})).status_code == 403

    admin = await login_as(client, "admin")
    entry = (await _log(client, admin, "stage.progress"))[0]
    assert entry["actorRole"] == "manager" and "80% (было" in entry["summary"]


def test_plan_progress_is_even_by_days():
    from datetime import date

    from app.models import Stage

    stage = Stage(start_date=date(2026, 10, 1), end_date=date(2026, 10, 10))
    assert stage.plan_progress_on(date(2026, 9, 30)) == 0
    assert stage.plan_progress_on(date(2026, 10, 1)) == 10  # к концу первого из десяти дней
    assert stage.plan_progress_on(date(2026, 10, 5)) == 50
    assert stage.plan_progress_on(date(2026, 10, 10)) == 100 and stage.plan_progress_on(date(2026, 12, 1)) == 100


async def test_staff_and_passwords(client):
    admin = await login_as(client, "admin")
    body = {
        "login": "prorab5",
        "name": "Лебедев Олег",
        "role": "foreman",
        "phone": "+7 900 000-00-00",
        "siteIds": ["s4"],
        "password": "first-pass",
    }
    created = await client.post("/api/users", headers=admin, json=body)
    assert created.status_code == 201 and created.json()["siteIds"] == ["s4"]
    assert (await client.post("/api/users", headers=admin, json=body)).status_code == 409  # логин занят
    new_id = created.json()["id"]

    assert (await client.post("/api/auth/login", json={"login": "prorab5", "password": "first-pass"})).status_code == 200
    assert (
        await client.post(f"/api/users/{new_id}/password", headers=admin, json={"password": "changed-pass"})
    ).status_code == 204
    assert (await client.post("/api/auth/login", json={"login": "prorab5", "password": "first-pass"})).status_code == 401
    assert (await client.post("/api/auth/login", json={"login": "prorab5", "password": "changed-pass"})).status_code == 200

    moved = await client.patch(f"/api/users/{new_id}", headers=admin, json={"role": "manager", "siteIds": []})
    assert moved.json()["role"] == "manager"
    off = await client.patch(f"/api/users/{new_id}", headers=admin, json={"isActive": False})
    assert off.json()["isActive"] is False
    assert (await client.post("/api/auth/login", json={"login": "prorab5", "password": "changed-pass"})).status_code == 401

    log = (await client.get("/api/audit?action=user", headers=admin)).json()  # user.* — без записей о входе
    assert "changed-pass" not in json.dumps(log, ensure_ascii=False) and "first-pass" not in json.dumps(log, ensure_ascii=False)
    assert [e["action"] for e in log][:4] == ["user.update", "user.update", "user.password", "user.create"]
    assert log[0]["summary"].startswith("Отключил") and log[1]["details"]["role"] == ["foreman", "manager"]
    assert (await client.delete(f"/api/users/{new_id}", headers=admin)).status_code == 204


async def test_admin_cannot_lock_everyone_out(client):
    admin = await login_as(client, "admin")
    assert (await client.patch("/api/users/u7", headers=admin, json={"role": "manager"})).status_code == 409  # сам себя
    assert (await client.delete("/api/users/u7", headers=admin)).status_code == 409
    second = await client.post(
        "/api/users", headers=admin, json={"login": "admin2", "name": "Второй Админ", "role": "admin", "password": "admin2-pass"}
    )
    other = await client.post("/api/auth/login", json={"login": "admin2", "password": "admin2-pass"})
    other_auth = {"Authorization": f"Bearer {other.json()['token']}"}
    # теперь админов двое: второй может снять первого, но не себя последнего
    assert (await client.patch("/api/users/u7", headers=other_auth, json={"isActive": False})).status_code == 200
    assert (await client.delete(f"/api/users/{second.json()['id']}", headers=other_auth)).status_code == 409


async def test_failed_login_is_logged(client):
    await client.post("/api/auth/login", json={"login": "admin", "password": "guess"})
    admin = await login_as(client, "admin")
    entry = (await _log(client, admin, "login.failed"))[0]
    assert entry["actorLogin"] == "admin" and entry["actorName"] == "" and "Неудачная попытка" in entry["summary"]


async def test_keycloak_mode_mirrors_staff_changes(client, monkeypatch):
    """Вход через Keycloak: сотрудник, роль и пароль меняются и в Keycloak (подменённый Admin API)."""
    from app import keycloak_admin as ka
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "keycloak_issuer", "http://kc.test/realms/stroykontrol")
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", "backend-secret")
    calls: list[tuple[str, str, object]] = []
    users: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        is_json = request.headers.get("content-type", "").startswith("application/json")
        path, body = request.url.path, json.loads(request.content) if is_json and request.content else None
        calls.append((request.method, path, body))
        if path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        if path.endswith("/users") and request.method == "GET":
            found = users.get(request.url.params["username"])
            return httpx.Response(200, json=[found] if found else [])
        if path.endswith("/users") and request.method == "POST":
            users[body["username"]] = {"id": f"kc-{body['username']}", **body}
            return httpx.Response(201, headers={"Location": f"http://kc.test/admin/realms/x/users/kc-{body['username']}"})
        if path.endswith("/role-mappings/realm") and request.method == "GET":
            return httpx.Response(200, json=[])
        if "/roles/" in path:
            return httpx.Response(200, json={"id": "r1", "name": path.rsplit("/", 1)[1]})
        return httpx.Response(204)

    monkeypatch.setattr(ka, "_admin", ka.KeycloakAdmin(transport=httpx.MockTransport(handler)))
    admin_headers = {
        "Authorization": f"Bearer {(await client.post('/api/auth/login', json={'login': 'admin', 'password': 'stand-password'})).json()['token']}"
    }

    created = await client.post(
        "/api/users",
        headers=admin_headers,
        json={"login": "inspektor2", "name": "Павлов Иван", "role": "inspector", "password": "secret-12"},
    )
    assert created.status_code == 201, created.text
    posted = next(body for method, path, body in calls if method == "POST" and path.endswith("/users"))
    assert posted["firstName"] == "Иван" and posted["lastName"] == "Павлов" and posted["credentials"][0]["value"] == "secret-12"
    assert any(path.endswith("/role-mappings/realm") and method == "POST" for method, path, _ in calls)

    # у prorab2 в Keycloak учётки не было — смена пароля заводит её
    assert (
        await client.post("/api/users/u2/password", headers=admin_headers, json={"password": "new-pass-1"})
    ).status_code == 204
    assert "prorab2" in users and any(path.endswith("/reset-password") for _, path, _ in calls)


async def test_keycloak_admin_renews_token_after_keycloak_restart(monkeypatch):
    """Keycloak перезапустили: старый токен служебного клиента отвергается — берём новый и повторяем запрос."""
    from app import keycloak_admin as ka
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "keycloak_issuer", "http://kc.test/realms/stroykontrol")
    issued: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            issued.append(f"t{len(issued)}")
            return httpx.Response(200, json={"access_token": issued[-1], "expires_in": 300})
        if request.headers["authorization"] == "Bearer t0":  # токен от прежнего экземпляра Keycloak
            return httpx.Response(401, json={"error": "HTTP 401 Unauthorized"})
        return httpx.Response(200, json=[])

    admin = ka.KeycloakAdmin(transport=httpx.MockTransport(handler))
    assert await admin.find("prorab") is None
    assert issued == ["t0", "t1"]


async def test_keycloak_admin_uses_internal_backchannel(monkeypatch):
    from app import keycloak_admin as ka
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "keycloak_issuer", "http://localhost:8180/realms/stroykontrol")
    monkeypatch.setattr(settings, "keycloak_backchannel_url", "http://keycloak:8080")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        return httpx.Response(200, json=[])

    admin = ka.KeycloakAdmin(transport=httpx.MockTransport(handler))
    assert await admin.find("prorab") is None
    assert requested == [
        "http://keycloak:8080/realms/stroykontrol/protocol/openid-connect/token",
        "http://keycloak:8080/admin/realms/stroykontrol/users?username=prorab&exact=true",
    ]


async def test_site_progress_comes_from_plan(client):
    """Проценты объекта не вводят руками: по графику и по факту — по всем работам плана с весом по длительности."""
    from datetime import timedelta

    from app.db import utcnow
    from app.services.engine import local_day

    manager = await login_as(client, "manager")
    site = (await client.post("/api/sites", headers=manager, json={"name": "Новый объект"})).json()
    assert site["planProgress"] is None and site["factProgress"] is None  # плана ещё нет

    today = local_day(utcnow())
    phase = await client.post(
        f"/api/sites/{site['id']}/stages",
        headers=manager,
        json={
            "name": "Земляные работы",
            "level": 1,
            "start": str(today - timedelta(days=4)),
            "end": str(today + timedelta(days=5)),
        },
    )
    work = await client.post(
        f"/api/sites/{site['id']}/stages",
        headers=manager,
        json={
            "name": "Котлован", "level": 2, "parentId": phase.json()["id"],
            "start": str(today - timedelta(days=4)), "end": str(today + timedelta(days=5)),
        },
    )  # fmt: skip
    await client.patch(f"/api/stages/{work.json()['id']}/progress", headers=manager, json={"factProgress": 20})
    listed = {s["id"]: s for s in (await client.get("/api/sites", headers=manager)).json()}[site["id"]]
    assert listed["planProgress"] == 50 and listed["factProgress"] == 20  # 5-й из 10 дней — половина по графику
