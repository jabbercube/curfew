"""Boot-time configuration for curfew-core.

Distinct from the runtime ``settings`` table (see ``core/curfew/models.py``).
This module's ``Settings`` class is **boot-time and immutable** for the life of
the process — it tells the service where the database lives, what port to bind,
what root token to accept. The ``settings`` table holds **runtime-mutable**
operator knobs (tick rates, retention windows). Same English word, different
concepts (per ADR-011).

Source precedence (highest wins): process env > ``.env`` > ``config.json`` >
defaults. Secrets (``root_token``) live in env only — ``config.json`` is allowed
to be checked into the operator's deployment repo, so it must never carry a
bearer.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class Settings(BaseSettings):
    """Boot-time settings sourced from env + ``.env`` + ``config.json``."""

    model_config = SettingsConfigDict(
        env_prefix="CURFEW_",
        env_file=".env",
        env_file_encoding="utf-8",
        json_file="config.json",
        json_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: str = "state.sqlite"
    root_token: str = Field(
        ...,
        description="Operator bearer token. Env-only — never read from config.json.",
    )
    listen_host: str = "0.0.0.0"
    listen_port: int = 8000
    agent_base_url: str = ""
    plugins_dirs: str = "plugins"  # colon-separated, PATH-style
    log_level: str = "info"
    cors_origins: list[str] = Field(default_factory=list)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Layer order: env > .env > config.json > class defaults.

        Wraps the JSON source to forbid ``root_token`` (secrets are env-only).
        """
        json_source = _RootTokenForbiddenJsonSource(settings_cls)
        return (env_settings, dotenv_settings, json_source, init_settings)


class _RootTokenForbiddenJsonSource(JsonConfigSettingsSource):
    """JSON source that refuses to load a ``root_token`` from ``config.json``."""

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        if "root_token" in data:
            raise ValueError(
                "root_token must come from CURFEW_ROOT_TOKEN env, not config.json. "
                "Remove 'root_token' from config.json."
            )
        return data


@lru_cache(maxsize=1)
def get_config() -> Settings:
    """Return the process-wide Settings, cached after first load."""
    return Settings()  # type: ignore[call-arg]  # root_token is sourced from env


def reset_config_cache() -> None:
    """Clear the cached Settings; useful in tests that monkeypatch env vars."""
    get_config.cache_clear()
