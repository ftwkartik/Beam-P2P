"""Application settings.

All configuration is environment-driven (twelve-factor style) via pydantic-settings.
Nothing is hardcoded the way [redacted] hardcodes its PORT and CORS origins (see
docs/reference-analysis.md). Secrets are `SecretStr` and are validated for strength
outside test environments so a weak or default secret can never reach production
silently (see docs/security.md, "Secrets and error handling").
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
