from __future__ import annotations

import secrets
from uuid import UUID

import jwt
from fastapi import Header, Request, Security
from fastapi.security import APIKeyCookie, APIKeyHeader

from marketplace.shared.errors import DomainError, forbidden
from marketplace.shared.schemas import Identity

internal_key_scheme = APIKeyHeader(name="X-Service-Key", auto_error=False)


async def internal(request: Request, service_key: str | None = Security(internal_key_scheme)):
    supplied = service_key or ""
    expected = request.app.state.settings.service_key
    if not expected or not secrets.compare_digest(supplied, expected):
        raise DomainError(401, "UNAUTHORIZED", "Требуется межсервисная авторизация")


cookie_scheme = APIKeyCookie(name="auth-token", auto_error=False)


async def identity(
    request: Request,
    auth_cookie: str | None = Security(cookie_scheme),
    dev_user_id: str | None = Header(
        None, alias="X-User-Id", description="Development only; ignored when AUTH_DEV_MODE=false"
    ),
) -> Identity:
    settings = request.app.state.settings
    if settings.auth_dev_mode and dev_user_id is not None:
        try:
            uid = UUID(dev_user_id)
        except ValueError as exc:
            raise DomainError(401, "UNAUTHORIZED", "Некорректный пользователь") from exc
        return await request.app.state.users.dev_identity(uid)
    token = auth_cookie
    if not token:
        raise DomainError(401, "UNAUTHORIZED", "Необходима авторизация")
    try:
        claims = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "iss", "aud"]},
        )
        uid = UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise DomainError(401, "UNAUTHORIZED", "Недействительная сессия") from exc
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        # A mandatory allowlisted Origin plus a non-simple header prevents cross-site cookie writes.
        if (
            request.headers.get("Origin") not in settings.cors_origins
            or request.headers.get("X-CSRF-Protection") != "1"
        ):
            forbidden("Не пройдена проверка CSRF")
    user = await request.app.state.users.current(token)
    if user.id != uid:
        raise DomainError(401, "UNAUTHORIZED", "Несогласованная сессия")
    return user
