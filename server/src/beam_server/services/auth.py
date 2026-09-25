"""Register/login/refresh/logout orchestration (Milestone 11).

Thin on purpose: password verification and token issuance already live in
security/passwords.py and security/auth_tokens.py, and row access already lives in
db/repositories.py. This just sequences those pieces and is what api/auth.py calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from beam_server.db.models import User
from beam_server.db.repositories import RefreshTokenRepository, UserRepository
from beam_server.errors import ConflictError, UnauthorizedError
from beam_server.security.auth_tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from beam_server.security.passwords import hash_password, verify_password


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str


class AuthService:
    def __init__(
        self,
        *,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        jwt_secret: str,
        access_token_ttl_seconds: int,
        refresh_token_ttl_seconds: int,
    ) -> None:
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._jwt_secret = jwt_secret
        self._access_ttl = access_token_ttl_seconds
        self._refresh_ttl = refresh_token_ttl_seconds

    async def register(self, *, email: str, password: str) -> User:
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("An account with that email already exists.")
        return self._users.create(email=email, password_hash=hash_password(password))

    async def login(self, *, email: str, password: str) -> tuple[User, IssuedTokens]:
        user = await self._users.get_by_email(email)
        # Same error for "no such user" and "wrong password" -- which one it was
        # isn't something a guesser should learn (the same principle docs/security.md
        # already applies to room codes).
        if user is None or not verify_password(password_hash=user.password_hash, password=password):
            raise UnauthorizedError("Incorrect email or password.")
        return user, await self._issue_tokens(user.id)

    async def refresh(self, raw_refresh_token: str) -> IssuedTokens:
        token_hash = hash_refresh_token(raw_refresh_token)
        token = await self._refresh_tokens.get_by_hash(token_hash)
        now = datetime.now(UTC)
        if token is None or token.revoked_at is not None or token.expires_at < now:
            raise UnauthorizedError("Refresh token is invalid or expired.")
        await self._refresh_tokens.revoke(token, revoked_at=now)
        return await self._issue_tokens(token.user_id)

    async def logout(self, raw_refresh_token: str) -> None:
        token = await self._refresh_tokens.get_by_hash(hash_refresh_token(raw_refresh_token))
        if token is not None and token.revoked_at is None:
            await self._refresh_tokens.revoke(token, revoked_at=datetime.now(UTC))

    async def _issue_tokens(self, user_id: uuid.UUID) -> IssuedTokens:
        access_token = create_access_token(
            secret=self._jwt_secret, user_id=str(user_id), ttl_seconds=self._access_ttl
        )
        raw_refresh = generate_refresh_token()
        self._refresh_tokens.create(
            user_id=user_id,
            token_hash=hash_refresh_token(raw_refresh),
            expires_at=datetime.now(UTC) + timedelta(seconds=self._refresh_ttl),
        )
        return IssuedTokens(access_token=access_token, refresh_token=raw_refresh)
