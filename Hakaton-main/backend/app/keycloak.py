"""Проверка токенов Keycloak (OpenID Connect).

Включается настройкой SK_KEYCLOAK_ISSUER. Тогда бэкенд принимает и свои токены (HS256), и токены Keycloak (RS256):
подпись Keycloak-токена проверяется публичными ключами realm (JWKS), которые скачиваются один раз и кэшируются.
Роль берётся из realm-ролей токена, привязка к объектам — из базы по логину (preferred_username).
"""

import asyncio
import json
import time

import httpx
import jwt

from app.config import get_settings

settings = get_settings()

# Наши роли в порядке приоритета: если Keycloak выдал пользователю несколько, берём старшую
ROLE_PRIORITY = ("admin", "inspector", "manager", "foreman")
_JWKS_TTL = 3600.0
# токен с незнакомым kid перечитывает ключи не чаще раза в 30 с — иначе мусорные токены гоняли бы нас в Keycloak
_FORCED_REFRESH_GAP = 30.0


class KeycloakError(Exception):
    """Токен Keycloak не прошёл проверку."""


class KeycloakUnavailable(KeycloakError):
    """Keycloak не ответил — токен проверить нечем. Это не повод выкидывать пользователя из системы."""


class KeycloakVerifier:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        timeout: float = 8.0,
        *,
        backchannel_issuer: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.backchannel_issuer = (backchannel_issuer or issuer).rstrip("/")
        self.client_id = client_id
        self.timeout = timeout
        self._transport = transport
        self._keys: dict[str, object] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def _cache_ok(self, force: bool) -> bool:
        age = time.monotonic() - self._fetched_at
        return bool(self._keys) and age < (_FORCED_REFRESH_GAP if force else _JWKS_TTL)

    def _backchannel_jwks_uri(self, jwks_uri: str) -> str:
        """Заменить только публичный realm-префикс discovery на внутренний endpoint."""
        if self.backchannel_issuer == self.issuer:
            return jwks_uri
        prefix = f"{self.issuer}/"
        if not jwks_uri.startswith(prefix):
            raise ValueError("Keycloak discovery вернул JWKS вне настроенного issuer")
        return f"{self.backchannel_issuer}{jwks_uri[len(self.issuer):]}"

    async def _load_keys(self, *, force: bool = False) -> None:
        if self._cache_ok(force):
            return
        async with self._lock:
            if self._cache_ok(force):
                return
            try:
                async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                    conf = (await client.get(f"{self.backchannel_issuer}/.well-known/openid-configuration")).json()
                    jwks = (await client.get(self._backchannel_jwks_uri(conf["jwks_uri"]))).json()
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                if self._keys and not force:
                    return  # Keycloak прилёг, а ключи уже есть: проверяем по ним, чем отвечать всем 503
                raise KeycloakUnavailable(f"Не удалось получить ключи Keycloak: {exc}") from exc
            keys: dict[str, object] = {}
            for k in jwks.get("keys", []):
                if k.get("kty") == "RSA" and k.get("use", "sig") == "sig":
                    keys[k["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
            if keys:
                self._keys = keys
                self._fetched_at = time.monotonic()

    async def verify(self, token: str) -> dict:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise KeycloakError("Плохой заголовок токена") from exc

        await self._load_keys()
        key = self._keys.get(kid)
        if key is None:  # ключи Keycloak могли смениться — перечитаем один раз
            await self._load_keys(force=True)
            key = self._keys.get(kid)
        if key is None:
            raise KeycloakError("Неизвестный ключ подписи")

        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], issuer=self.issuer, options={"verify_aud": False})
        except jwt.PyJWTError as exc:
            raise KeycloakError(f"Токен недействителен: {exc}") from exc

        # принимаем только токены нашего клиента (иначе токен другого приложения того же realm подошёл бы)
        aud = claims.get("aud")
        aud_ok = self.client_id in (aud if isinstance(aud, list) else [aud])
        if claims.get("azp") != self.client_id and not aud_ok:
            raise KeycloakError("Токен выдан другому приложению")
        return claims


def select_role(roles: set[str]) -> str | None:
    return next((r for r in ROLE_PRIORITY if r in roles), None)


def roles_from_claims(claims: dict) -> set[str]:
    realm = claims.get("realm_access", {}).get("roles", [])
    client = claims.get("resource_access", {}).get(settings.keycloak_client_id, {}).get("roles", [])
    return set(realm) | set(client)


_verifier: KeycloakVerifier | None = None


def get_verifier() -> KeycloakVerifier | None:
    global _verifier
    if not settings.keycloak_issuer:
        return None
    if _verifier is None:
        _verifier = KeycloakVerifier(
            settings.keycloak_issuer, settings.keycloak_client_id, backchannel_issuer=settings.keycloak_backchannel_issuer
        )
    return _verifier
