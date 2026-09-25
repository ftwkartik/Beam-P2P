"""Account endpoints: register, login, refresh, logout, me (Milestone 11).

Request/response bodies are co-located with the routes, matching api/rooms.py's
reasoning: Beam's REST surface is small enough that a separate schemas/ package would
just be an extra place to look, not real separation of concerns.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from beam_server.config import Settings, get_settings
from beam_server.db.engine import get_session
from beam_server.db.models import User
from beam_server.db.repositories import RefreshTokenRepository, UserRepository
from beam_server.errors import RateLimitedError, UnauthorizedError
from beam_server.security.auth_tokens import TokenError, decode_access_token
from beam_server.security.client_ip import get_client_ip
from beam_server.services.auth import AuthService
from beam_server.store.rate_limit import RateLimitExceeded, check_rate_limit
from beam_server.store.redis import get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

#: A password long enough to resist offline guessing of the argon2id hash if the DB
#: ever leaks, short enough not to reject a real passphrase.
MIN_PASSWORD_LENGTH = 8


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthService:
    return AuthService(
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        jwt_secret=settings.jwt_secret.get_secret_value(),
        access_token_ttl_seconds=settings.access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.refresh_token_ttl_seconds,
    )


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolves the signed-in user from a Bearer access token. Every route behind this
    is implicitly owner-scoped: handlers get a real `User.id` to filter on, never a
    client-supplied one (docs/security.md's isolation rule)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("A bearer access token is required.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = decode_access_token(secret=settings.jwt_secret.get_secret_value(), token=token)
    except TokenError as exc:
        raise UnauthorizedError("Invalid or expired access token.") from exc
    user = await UserRepository(session).get_by_id(uuid.UUID(claims.user_id))
    if user is None:
        raise UnauthorizedError("Invalid or expired access token.")
    return user


async def _enforce_rate_limit(
    redis_client: redis.Redis, *, scope: str, key: str, limit: int, window_seconds: int
) -> None:
    try:
        await check_rate_limit(
            redis_client, scope=scope, key=key, limit=limit, window_seconds=window_seconds
        )
    except RateLimitExceeded as exc:
        raise RateLimitedError(
            "Too many requests. Please slow down.", retry_after=exc.retry_after
        ) from exc


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 -- the OAuth2 token-type field, not a secret


class UserResponse(BaseModel):
    id: str
    email: str


@router.post("/register", status_code=201, response_model=UserResponse)
async def register(
    request: Request,
    body: RegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UserResponse:
    client_ip = get_client_ip(request, settings.trusted_proxies)
    await _enforce_rate_limit(
        redis_client,
        scope="register_ip",
        key=client_ip,
        limit=settings.rate_limit_register_per_hour,
        window_seconds=60 * 60,
    )
    user = await auth_service.register(email=body.email, password=body.password)
    await session.commit()
    logger.info("user_registered", user_id=str(user.id))
    return UserResponse(id=str(user.id), email=user.email)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    body: LoginRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    client_ip = get_client_ip(request, settings.trusted_proxies)
    await _enforce_rate_limit(
        redis_client,
        scope="login_ip",
        key=client_ip,
        limit=settings.rate_limit_login_per_minute,
        window_seconds=60,
    )
    user, tokens = await auth_service.login(email=body.email, password=body.password)
    await session.commit()
    logger.info("user_logged_in", user_id=str(user.id))
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    tokens = await auth_service.refresh(body.refresh_token)
    await session.commit()
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await auth_service.logout(body.refresh_token)
    await session.commit()


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return UserResponse(id=str(user.id), email=user.email)
