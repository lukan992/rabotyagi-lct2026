"""Управление пользователями в Keycloak из нашей админки (Admin REST API).

Когда вход идёт через Keycloak, пароли и роли живут там. Администратор меняет их у нас, а сервер передаёт в Keycloak
от имени служебного клиента (client credentials) с правами manage-users. Клиент и его секрет — в настройках
SK_KEYCLOAK_ADMIN_CLIENT_ID / SK_KEYCLOAK_ADMIN_CLIENT_SECRET (для разработки заведены в infra/keycloak/realm-*.json).
"""

import time

import httpx

from app.config import get_settings
from app.keycloak import ROLE_PRIORITY

settings = get_settings()


class KeycloakAdminError(Exception):
    """Keycloak не выполнил действие. message — понятная фраза для интерфейса."""


def _split_name(full: str) -> tuple[str, str]:
    """«Кузнецов Андрей» → (имя «Андрей», фамилия «Кузнецов»): у нас сначала фамилия."""
    parts = full.split(maxsplit=1)
    return (parts[1], parts[0]) if len(parts) == 2 else (full, "")


class KeycloakAdmin:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = f"{settings.keycloak_backchannel_url or settings.keycloak_url}/admin/realms/{settings.keycloak_realm}"
        self._token, self._token_until = "", 0.0
        self._transport = transport

    async def _client(self) -> httpx.AsyncClient:
        if time.monotonic() >= self._token_until:
            async with httpx.AsyncClient(timeout=8.0, transport=self._transport) as client:
                try:
                    response = await client.post(
                        f"{settings.keycloak_backchannel_issuer}/protocol/openid-connect/token",
                        data={
                            "grant_type": "client_credentials",
                            "client_id": settings.keycloak_admin_client_id,
                            "client_secret": settings.keycloak_admin_client_secret or "",
                        },
                    )
                except httpx.HTTPError as exc:
                    raise KeycloakAdminError("Keycloak не отвечает — изменения не переданы. Попробуйте позже.") from exc
            if response.status_code != 200:
                raise KeycloakAdminError("Keycloak не пустил служебный клиент: проверьте SK_KEYCLOAK_ADMIN_CLIENT_SECRET")
            data = response.json()
            self._token, self._token_until = data["access_token"], time.monotonic() + max(data.get("expires_in", 60) - 15, 5)
        return httpx.AsyncClient(
            base_url=self.base, timeout=8.0, transport=self._transport, headers={"Authorization": f"Bearer {self._token}"}
        )

    async def _call(self, method: str, url: str, *, retry: bool = True, **kwargs) -> httpx.Response:
        async with await self._client() as client:
            try:
                response = await client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                raise KeycloakAdminError("Keycloak не отвечает — изменения не переданы. Попробуйте позже.") from exc
        if response.status_code == 401 and retry:
            # Keycloak перезапустили или сменили ключи — старый токен служебного клиента больше не годится
            self._token_until = 0.0
            return await self._call(method, url, retry=False, **kwargs)
        if response.status_code == 409:
            raise KeycloakAdminError("В Keycloak уже есть пользователь с таким логином")
        if response.status_code == 400 and "password" in response.text.lower():
            raise KeycloakAdminError("Keycloak не принял пароль: он не подходит под политику паролей")
        if response.status_code >= 400 and not (method == "DELETE" and response.status_code == 404):
            raise KeycloakAdminError(f"Keycloak отказал ({response.status_code}): {response.text[:160]}")
        return response

    async def find(self, username: str) -> dict | None:
        users = (await self._call("GET", "/users", params={"username": username, "exact": "true"})).json()
        return users[0] if users else None

    async def create(self, *, username: str, name: str, enabled: bool, password: str | None, role: str) -> str:
        first, last = _split_name(name)
        body: dict = {"username": username, "enabled": enabled, "firstName": first, "lastName": last, "emailVerified": True}
        if password:
            body["credentials"] = [{"type": "password", "value": password, "temporary": False}]
        response = await self._call("POST", "/users", json=body)
        user_id = response.headers.get("Location", "").rstrip("/").rsplit("/", 1)[-1]
        await self.set_role(user_id, role)
        return user_id

    async def update(self, user_id: str, *, name: str, enabled: bool) -> None:
        first, last = _split_name(name)
        await self._call("PUT", f"/users/{user_id}", json={"firstName": first, "lastName": last, "enabled": enabled})

    async def set_password(self, user_id: str, password: str) -> None:
        await self._call(
            "PUT", f"/users/{user_id}/reset-password", json={"type": "password", "value": password, "temporary": False}
        )

    async def set_role(self, user_id: str, role: str) -> None:
        """Оставить пользователю ровно одну нашу роль (остальные роли realm не трогаем)."""
        current = (await self._call("GET", f"/users/{user_id}/role-mappings/realm")).json()
        extra = [r for r in current if r["name"] in ROLE_PRIORITY and r["name"] != role]
        if extra:
            await self._call("DELETE", f"/users/{user_id}/role-mappings/realm", json=extra)
        if not any(r["name"] == role for r in current):
            wanted = (await self._call("GET", f"/roles/{role}")).json()
            await self._call("POST", f"/users/{user_id}/role-mappings/realm", json=[wanted])

    async def delete(self, user_id: str) -> None:
        await self._call("DELETE", f"/users/{user_id}")

    async def ensure(self, *, username: str, name: str, enabled: bool, role: str, password: str | None = None) -> str:
        """Найти пользователя в Keycloak или завести (например, сотрудник был только у нас) — вернуть его id."""
        found = await self.find(username)
        if found:
            return found["id"]
        return await self.create(username=username, name=name, enabled=enabled, password=password, role=role)


_admin: KeycloakAdmin | None = None


def get_keycloak_admin() -> KeycloakAdmin | None:
    """None — Keycloak выключен: пользователи и пароли только у нас."""
    global _admin
    if settings.auth_mode != "keycloak":
        return None
    if not settings.keycloak_admin_client_secret:
        raise KeycloakAdminError(
            "Вход идёт через Keycloak, а доступ сервера к нему не настроен (SK_KEYCLOAK_ADMIN_CLIENT_SECRET) — "
            "менять сотрудников и пароли пока можно только в консоли Keycloak"
        )
    if _admin is None:
        _admin = KeycloakAdmin()
    return _admin
