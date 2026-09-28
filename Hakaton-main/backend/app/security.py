"""Доступ: пароли (scrypt из стандартной библиотеки), токены JWT, роли, шифрование паролей камер."""

import asyncio
import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Annotated

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session, utcnow
from app.keycloak import KeycloakError, KeycloakUnavailable, get_verifier, roles_from_claims, select_role
from app.models import User, new_id

settings = get_settings()
_bearer = HTTPBearer(auto_error=False)

# ---------- пароли пользователей ----------
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=_N, r=_R, p=_P, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# хэш для несуществующего логина: проверяем пароль всегда, чтобы по времени ответа нельзя было узнать, есть ли такой логин
_DUMMY_HASH = hash_password(secrets.token_hex(8))


async def check_password(password: str, stored: str | None) -> bool:
    """scrypt занимает ~25 мс процессора — считаем в отдельном потоке, не останавливая сервер."""
    ok = await asyncio.to_thread(verify_password, password, stored or _DUMMY_HASH)
    return ok and stored is not None


# ---------- пароли камер: шифруем, чтобы в базе не лежали в открытом виде ----------
def _fernet() -> Fernet:
    key = hashlib.sha256(f"camera-credentials:{settings.secret_key}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None  # ключ сменили — пароль придётся ввести заново


# ---------- токены ----------
# Ключ подписи всегда 32 байта, какой бы длины ни был SK_SECRET_KEY
_JWT_KEY = hashlib.sha256(f"jwt:{settings.secret_key}".encode()).digest()


def create_token(user: User) -> str:
    payload = {"sub": user.id, "role": user.role, "exp": utcnow() + timedelta(hours=settings.token_ttl_hours)}
    return jwt.encode(payload, _JWT_KEY, algorithm="HS256")


async def _user_from_keycloak(session: AsyncSession, claims: dict) -> User:
    """Сопоставить пользователя Keycloak с записью в базе (по логину) или собрать временного по роли из токена.

    Роль всегда из Keycloak: ролями управляют там. Из базы — только привязка к объектам.
    """
    role = select_role(roles_from_claims(claims))
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "У пользователя нет роли для СтройКонтроля")
    username = (claims.get("preferred_username") or "").strip().lower()
    user = await session.scalar(select(User).where(User.login == username)) if username else None
    if user is not None:
        if not user.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Учётная запись отключена администратором")
        if user.role != role:  # роль поменяли в Keycloak — переносим в базу, чтобы и список сотрудников показывал её
            user.role = role
            await session.commit()
        return user
    if not settings.keycloak_auto_provision:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Пользователь не заведён в системе")
    # временный пользователь: роль из Keycloak, объектов нет (руководителю/инспектору/админу они и не нужны)
    user = User(
        id=new_id("kc"),
        login=username or claims.get("sub", "keycloak"),
        name=claims.get("name") or username or "Пользователь Keycloak",
        role=role,
        phone="",
        password_hash="",
        is_active=True,
    )
    user.sites = []
    return user


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужно войти в систему")
    return await authenticate(session, credentials.credentials)


async def authenticate(session: AsyncSession, token: str) -> User:
    """Пользователь по токену — своему (HS256) или Keycloak (RS256). Нужен и API, и шлюзу видео («кому показывать поток»)."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужно войти в систему")
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
    except jwt.PyJWTError:
        raise unauthorized from None
    if not isinstance(alg, str):  # "alg": null или число раньше давали 500
        raise unauthorized

    verifier = get_verifier()
    if alg.startswith("RS") and verifier is not None:  # токен Keycloak
        try:
            claims = await verifier.verify(token)
        except KeycloakUnavailable:  # 401 выкинул бы пользователя, хотя токен, возможно, в порядке
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Сервер входа Keycloak не отвечает. Попробуйте чуть позже."
            ) from None
        except KeycloakError:
            raise unauthorized from None
        return await _user_from_keycloak(session, claims)

    try:  # свой токен (HS256)
        payload = jwt.decode(token, _JWT_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise unauthorized from None
    user = await session.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(current_user)]
Session = Annotated[AsyncSession, Depends(get_session)]


def require_roles(*roles: str):
    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для этого действия")
        return user

    return Depends(checker)


# Кто подключает новые камеры (с проверкой адреса и живым предпросмотром). Менять и удалять камеры — только администратор.
CAMERA_ADDERS = ("admin", "manager")
# Кто добавляет и настраивает объекты: карточка, зоны, календарный план. Удалить объект — только администратор.
SITE_MANAGERS = ("admin", "manager")
# Кто отмечает, сколько работ сделано по факту. Прораб — нет: он видит план и выполнение, но не отмечает его
PROGRESS_REPORTERS = ("admin", "manager")


def visible_site_ids(user: User) -> set[str] | None:
    """Прораб видит только свои объекты; остальные роли — все (None = без ограничения)."""
    return {s.id for s in user.sites} if user.role == "foreman" else None


def ensure_site_access(user: User, site_id: str) -> None:
    allowed = visible_site_ids(user)
    if allowed is not None and site_id not in allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Объект не найден")
