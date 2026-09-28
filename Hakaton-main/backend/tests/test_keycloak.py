"""Приём токенов Keycloak (RS256) наряду со своими (HS256). Keycloak не запускается — ключи и токен делаем сами."""

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import login_as

pytestmark = pytest.mark.anyio

ISSUER = "http://kc.test/realms/stroykontrol"
CLIENT = "stroykontrol-web"
_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_KID = "test-kid"


def _make_token(**over) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": "kc-sub-1",
        "azp": CLIENT,
        "aud": "account",
        "exp": now + 300,
        "iat": now,
        "preferred_username": "prorab",
        "name": "Кузнецов Андрей",
        "realm_access": {"roles": ["foreman", "offline_access"]},
    }
    payload.update(over)
    return jwt.encode(payload, _key, algorithm="RS256", headers={"kid": _KID})


@pytest.fixture
def keycloak(monkeypatch):
    """Включаем Keycloak и подменяем загрузку публичных ключей нашим ключом."""
    from app import keycloak as kc
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "keycloak_issuer", ISSUER)
    monkeypatch.setattr(settings, "keycloak_client_id", CLIENT)
    monkeypatch.setattr(kc.settings, "keycloak_issuer", ISSUER)
    monkeypatch.setattr(kc.settings, "keycloak_client_id", CLIENT)

    verifier = kc.KeycloakVerifier(ISSUER, CLIENT)
    verifier._keys = {_KID: jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(_jwk()))}
    verifier._fetched_at = time.monotonic() + 1e6  # чтобы не ходил в сеть
    monkeypatch.setattr(kc, "_verifier", verifier)
    monkeypatch.setattr(kc, "get_verifier", lambda: verifier)
    return verifier


def _jwk() -> dict:
    from jwt.algorithms import RSAAlgorithm

    return {**json.loads(RSAAlgorithm.to_jwk(_key.public_key())), "kid": _KID, "use": "sig", "alg": "RS256"}


async def test_meta_reports_keycloak(client, keycloak):
    meta = (await client.get("/api/meta")).json()
    assert meta["authMode"] == "keycloak"
    assert meta["keycloak"] == {"url": "http://kc.test", "realm": "stroykontrol", "clientId": CLIENT}


async def test_keycloak_token_maps_to_seeded_user_with_sites(client, keycloak):
    # preferred_username=prorab совпадает с посевным логином → получает его объект s1
    headers = {"Authorization": f"Bearer {_make_token()}"}
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "foreman" and me.json()["siteIds"] == ["s1"]
    sites = (await client.get("/api/sites", headers=headers)).json()
    assert {s["id"] for s in sites} == {"s1"}


async def test_keycloak_unknown_user_is_provisioned_by_role(client, keycloak):
    token = _make_token(
        preferred_username="newmanager", sub="kc-sub-2", realm_access={"roles": ["manager"]}, name="Новый Руководитель"
    )
    me = (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert me["role"] == "manager" and me["siteIds"] == []
    # руководителю объекты не нужны для доступа — видит все
    sites = (await client.get("/api/sites", headers={"Authorization": f"Bearer {token}"})).json()
    assert len(sites) == 4


async def test_bad_and_foreign_tokens_rejected(client, keycloak):
    assert (await client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})).status_code == 401
    other = _make_token(azp="other-app", aud="other-app")
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {other}"})).status_code == 401
    norole = _make_token(preferred_username="nobody", sub="kc-x", realm_access={"roles": ["offline_access"]})
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {norole}"})).status_code == 403


async def test_local_login_still_works_when_keycloak_enabled(client, keycloak):
    # свои HS256-токены принимаются одновременно с Keycloak
    foreman = await login_as(client, "foreman")
    assert (await client.get("/api/alerts", headers=foreman)).status_code == 200


async def test_role_comes_from_keycloak_and_is_synced_to_database(client, keycloak):
    # прорабу в Keycloak выдали роль руководителя: приложение слушается Keycloak, привязка к объекту остаётся своя
    me = (
        await client.get("/api/auth/me", headers={"Authorization": f"Bearer {_make_token(realm_access={'roles': ['manager']})}"})
    ).json()
    assert me["role"] == "manager" and me["siteIds"] == ["s1"]
    admin = await login_as(client, "admin")
    staff = {u["login"]: u for u in (await client.get("/api/users", headers=admin)).json()}
    assert staff["prorab"]["role"] == "manager"
    # роль вернули в Keycloak — вернулась и в приложении
    me = (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {_make_token()}"})).json()
    assert me["role"] == "foreman"


async def test_keycloak_down_is_503_not_logout(client, keycloak, monkeypatch):
    from app.keycloak import KeycloakUnavailable

    async def down(*, force: bool = False) -> None:
        raise KeycloakUnavailable("нет связи")

    monkeypatch.setattr(keycloak, "_load_keys", down)
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {_make_token()}"})
    assert response.status_code == 503  # не 401: фронтенд на 401 выходит из системы


async def test_disabled_user_is_told_so(client, keycloak):
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models import User

    async with SessionLocal() as session:
        await session.execute(update(User).where(User.login == "prorab").values(is_active=False))
        await session.commit()
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {_make_token()}"})
    assert response.status_code == 403 and "отключена" in response.json()["detail"]


async def test_unknown_kid_refetches_keys_at_most_every_30s():
    from app.keycloak import KeycloakVerifier

    verifier = KeycloakVerifier(ISSUER, CLIENT)
    verifier._keys = {"k": object()}
    verifier._fetched_at = time.monotonic() - 10
    assert verifier._cache_ok(force=True)  # только что перечитывали — мусорный kid в Keycloak не гоняет
    verifier._fetched_at = time.monotonic() - 40
    assert not verifier._cache_ok(force=True) and verifier._cache_ok(force=False)


async def test_without_backchannel_uses_public_discovery_and_jwks():
    from app.keycloak import KeycloakVerifier

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"})
        return httpx.Response(200, json={"keys": [_jwk()]})

    verifier = KeycloakVerifier(ISSUER, CLIENT, transport=httpx.MockTransport(handler))
    await verifier.verify(_make_token())
    assert requested == [
        f"{ISSUER}/.well-known/openid-configuration",
        f"{ISSUER}/protocol/openid-connect/certs",
    ]


async def test_backchannel_fetches_internal_jwks_but_validates_public_issuer():
    """Публичный iss не заставляет контейнер ходить через host-порт Keycloak."""
    from app.keycloak import KeycloakVerifier

    backchannel_issuer = "http://keycloak:8080/realms/stroykontrol"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"})
        if request.url.path.endswith("/protocol/openid-connect/certs"):
            return httpx.Response(200, json={"keys": [_jwk()]})
        return httpx.Response(404)

    verifier = KeycloakVerifier(
        ISSUER, CLIENT, backchannel_issuer=backchannel_issuer, transport=httpx.MockTransport(handler)
    )
    assert (await verifier.verify(_make_token()))["iss"] == ISSUER
    assert requested == [
        f"{backchannel_issuer}/.well-known/openid-configuration",
        f"{backchannel_issuer}/protocol/openid-connect/certs",
    ]


async def test_backchannel_rejects_jwks_outside_public_issuer():
    from app.keycloak import KeycloakUnavailable, KeycloakVerifier

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jwks_uri": "http://attacker.test/keys"})

    verifier = KeycloakVerifier(
        ISSUER,
        CLIENT,
        backchannel_issuer="http://keycloak:8080/realms/stroykontrol",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(KeycloakUnavailable, match="JWKS вне настроенного issuer"):
        await verifier._load_keys()
