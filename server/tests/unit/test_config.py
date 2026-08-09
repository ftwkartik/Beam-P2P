"""Settings validation: secrets must be strong outside ENV=test."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from beam_server.config import Settings


def test_test_env_allows_empty_secrets() -> None:
    settings = Settings(env="test")
    assert settings.jwt_secret.get_secret_value() == ""


def test_development_env_rejects_short_secrets() -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(env="development", jwt_secret="too-short")  # pragma: allowlist secret


def test_production_env_rejects_short_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(
            env="production",
            jwt_secret="a" * 32,
            code_pepper="short",
            turn_secret="a" * 32,
        )


def test_development_env_accepts_strong_secrets() -> None:
    settings = Settings(
        env="development",
        jwt_secret="a" * 32,
        code_pepper="b" * 32,
        turn_secret="c" * 32,
    )
    assert settings.jwt_secret.get_secret_value() == "a" * 32


def test_is_production_property() -> None:
    assert Settings(env="test").is_production is False
    assert (
        Settings(
            env="production", jwt_secret="a" * 32, code_pepper="b" * 32, turn_secret="c" * 32
        ).is_production
        is True
    )
