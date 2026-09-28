"""Защита от мусорного ввода и небезопасных настроек (по итогам ревью): вместо 500 — понятный отказ."""

import io

import pytest
from PIL import Image

from app.api import analyze
from app.config import Settings
from tests.conftest import login_as

pytestmark = pytest.mark.anyio


def test_production_refuses_public_secret_and_demo_seed():
    assert Settings(demo_mode=True).insecure_defaults() == []  # демо-стенд запускается как раньше
    # ingest_api_key=None: в тестах окружение задаёт публичный ключ приёма — его проверяет свой тест (test_usage)
    no_keys = {"ingest_api_key": None, "tracker_api_key": None}
    problems = Settings(demo_mode=False, secret_key="dev-only-secret-change-me", seed_on_start=True, **no_keys).insecure_defaults()
    assert len(problems) == 2
    assert Settings(demo_mode=False, secret_key="change-me-before-real-use", seed_on_start=False, **no_keys).insecure_defaults()
    assert Settings(demo_mode=False, secret_key="s" * 32, seed_on_start=False, **no_keys).insecure_defaults() == []


async def test_garbage_jwt_header_is_401_not_500(client):
    # {"alg": null} и {"alg": 1} в заголовке
    for header in ("eyJhbGciOm51bGx9", "eyJhbGciOjF9"):
        response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {header}.e30.c2ln"})
        assert response.status_code == 401


async def test_decompression_bomb_upload_is_rejected(client):
    # 12000×12000 — крошечный файл, но распакованный весил бы сотни мегабайт
    buffer = io.BytesIO()
    Image.new("1", (12000, 12000)).save(buffer, "PNG")
    manager = await login_as(client, "manager")
    response = await client.post(
        "/api/analyze", headers=manager, data={"siteId": "s1"}, files={"image": ("bomb.png", buffer.getvalue(), "image/png")}
    )
    assert response.status_code == 422


async def test_production_hides_samples_but_keeps_authenticated_upload(client, monkeypatch):
    manager = await login_as(client, "manager")
    monkeypatch.setattr(analyze.settings, "demo_mode", False)

    assert (await client.get("/api/analyze/samples", headers=manager)).status_code == 404
    assert (await client.post("/api/analyze", headers=manager, data={"sample": "pit-loading", "siteId": "s1"})).status_code == 404
    assert (await client.get("/api/public/demo")).status_code == 404
    assert (await client.post("/api/public/analyze", data={"ruleKey": "asphalt"})).status_code == 404

    photo = io.BytesIO()
    Image.new("RGB", (32, 32)).save(photo, "JPEG")
    response = await client.post(
        "/api/analyze", headers=manager, data={"siteId": "s1"}, files={"image": ("site.jpg", photo.getvalue(), "image/jpeg")}
    )
    assert response.status_code == 200


async def test_wrong_login_and_missing_login_answer_the_same(client):
    for login in ("admin", "no-such-user"):
        response = await client.post("/api/auth/login", json={"login": login, "password": "wrong"})
        assert response.status_code == 401 and response.json()["detail"] == "Неверный логин или пароль"
