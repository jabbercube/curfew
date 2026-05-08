"""Tests for ``core/curfew/config.py``.

Covers source precedence (env > .env > config.json > defaults), required-field
behaviour for ``root_token``, and the rule that ``config.json`` cannot supply
``root_token``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from curfew.config import Settings, reset_config_cache
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Each test starts with a fresh Settings cache."""
    reset_config_cache()


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> type[Settings]:
    """Run a Settings build inside ``tmp_path`` so .env / config.json are scoped."""
    monkeypatch.chdir(tmp_path)
    return Settings


def test_root_token_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CURFEW_ROOT_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_defaults_apply_when_only_token_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURFEW_ROOT_TOKEN", "abc")
    s = Settings()
    assert s.db_path == "state.sqlite"
    assert s.listen_port == 8000
    assert s.log_level == "info"
    assert s.cors_origins == []


def test_env_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURFEW_ROOT_TOKEN", "abc")
    monkeypatch.setenv("CURFEW_LISTEN_PORT", "9999")
    monkeypatch.setenv("CURFEW_DB_PATH", "/var/lib/curfew/state.sqlite")
    s = Settings()
    assert s.listen_port == 9999
    assert s.db_path == "/var/lib/curfew/state.sqlite"


def test_config_json_overrides_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURFEW_ROOT_TOKEN", "abc")
    (tmp_path / "config.json").write_text(json.dumps({"listen_port": 7777}))
    s = Settings()
    assert s.listen_port == 7777


def test_env_beats_config_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURFEW_ROOT_TOKEN", "abc")
    monkeypatch.setenv("CURFEW_LISTEN_PORT", "9999")
    (tmp_path / "config.json").write_text(json.dumps({"listen_port": 7777}))
    s = Settings()
    assert s.listen_port == 9999


def test_dotenv_beats_config_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CURFEW_ROOT_TOKEN", raising=False)
    monkeypatch.delenv("CURFEW_LISTEN_PORT", raising=False)
    (tmp_path / ".env").write_text("CURFEW_ROOT_TOKEN=abc\nCURFEW_LISTEN_PORT=8888\n")
    (tmp_path / "config.json").write_text(json.dumps({"listen_port": 7777}))
    s = Settings()
    assert s.listen_port == 8888


def test_root_token_forbidden_in_config_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURFEW_ROOT_TOKEN", "abc")
    (tmp_path / "config.json").write_text(json.dumps({"root_token": "from-json"}))
    with pytest.raises(ValueError, match="root_token must come from"):
        Settings()


def test_invalid_port_fails_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURFEW_ROOT_TOKEN", "abc")
    monkeypatch.setenv("CURFEW_LISTEN_PORT", "not-a-number")
    with pytest.raises(ValidationError):
        Settings()
