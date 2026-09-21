"""Application settings.

All configuration is environment-driven (twelve-factor style) via pydantic-settings --
nothing is hardcoded. Secrets are `SecretStr` and are validated for strength outside
test environments so a weak or default secret can never reach production silently
(see docs/security.md, "Secrets and error handling").
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Secrets shorter than this (bytes, as UTF-8) are rejected outside ENV=test.
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Runtime configuration, loaded from the environment and an optional `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    app_name: str = "Beam"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    host: str = "0.0.0.0"  # noqa: S104 - intentional bind-all inside a container
    port: int = 8000

    redis_url: str = "redis://localhost:6379/0"

    #: Origins allowed to open a WebSocket or call the API from a browser.
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    #: Reverse-proxy IPs trusted to set X-Forwarded-For (see docs/security.md §2).
    trusted_proxies: list[str] = Field(default_factory=list)

    #: Signs room tokens (JWT, HS256). Introduced for use in Milestone 3.
    jwt_secret: SecretStr = SecretStr("")
    #: Peppers room-code HMACs at rest. Introduced for use in Milestone 3.
    code_pepper: SecretStr = SecretStr("")
    #: Shared secret for minting ephemeral TURN credentials. Introduced in Milestone 6.
    turn_secret: SecretStr = SecretStr("")

    # --- Rooms (Milestone 3; see docs/data-model.md "Redis keyspace") --------------

    #: How long a room stays alive while waiting for a second peer.
    room_waiting_ttl_seconds: int = 30 * 60
    #: Hard cap on a paired room's lifetime, refreshed by activity up to this ceiling.
    room_paired_ttl_seconds: int = 24 * 60 * 60
    #: Wrong-code attempts allowed before a room is burned (docs/security.md §1).
    room_max_join_attempts: int = 5
    #: Room-token lifetime; bounded by the room's own expiry as well.
    room_token_ttl_seconds: int = 24 * 60 * 60

    #: STUN servers offered to every peer. A production deployment should run its own
    #: coturn (added in Milestone 6) rather than depend on a third party's STUN
    #: (docs/security.md §6, "Privacy").
    stun_servers: list[str] = Field(default_factory=lambda: ["stun:stun.l.google.com:19302"])

    # --- TURN (Milestone 6; docs/adr/004-turn-coturn-ephemeral-credentials.md) ------

    #: coturn URLs handed to clients alongside minted credentials. Empty by default
    #: (STUN-only) so a deployment without coturn configured doesn't advertise dead
    #: TURN endpoints; compose sets this to the bundled coturn service.
    turn_urls: list[str] = Field(default_factory=list)
    #: How long a minted TURN credential remains valid (docs/security.md §3: "valid
    #: for 1 hour").
    turn_credential_ttl_seconds: int = 60 * 60
    #: Per-room cap on ICE-server requests, since each one mints a fresh TURN
    #: credential (docs/security.md §3, "issued only to holders of a valid room
    #: token (rate limited per room)").
    rate_limit_ice_servers_per_room_per_minute: int = 10

    # --- Rate limits (docs/security.md §6) ------------------------------------------

    rate_limit_create_room_per_hour: int = 20
    rate_limit_join_per_minute: int = 10
    rate_limit_join_per_nameplate_per_minute: int = 20

    # --- Signaling WebSocket (Milestone 4; see docs/protocol.md §3) ------------------

    #: How long a client has to send `hello` after connecting.
    ws_hello_timeout_seconds: float = 5.0
    #: How long a disconnected peer's slot is held before the other peer is told
    #: they left for good (docs/protocol.md §3, "Connection lifecycle").
    ws_reconnect_grace_seconds: float = 30.0
    #: Token-bucket message rate limit per connection.
    ws_message_rate_per_second: float = 20.0
    ws_message_burst: int = 60
    #: Caps runaway ICE candidate spam within a single session.
    ws_max_ice_candidates_per_session: int = 200
    #: Passed to uvicorn at startup (see server/Dockerfile's CMD); the ASGI server
    #: owns the actual WebSocket ping/pong control frames, not the application.
    ws_ping_interval_seconds: int = 20
    ws_ping_timeout_seconds: int = 45

    @model_validator(mode="after")
    def _validate_secrets_outside_test(self) -> "Settings":
        if self.env == "test":
            return self
        for name in ("jwt_secret", "code_pepper", "turn_secret"):
            value = getattr(self, name).get_secret_value()
            if len(value.encode()) < MIN_SECRET_LENGTH:
                raise ValueError(
                    f"settings.{name} must be at least {MIN_SECRET_LENGTH} bytes "
                    f"outside ENV=test. Generate secrets with `scripts/gen_secrets.sh`."
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so environment parsing happens once per process. Tests override this via
    FastAPI's dependency-override mechanism rather than mutating the cache.
    """
    return Settings()
