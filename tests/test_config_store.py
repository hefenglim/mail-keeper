"""US1/US2/US3 — config.json bootstrap, loading, validation. Test-first."""
from __future__ import annotations

import json

import pytest

from mailkeeper import config, config_store


def _write(tmp, obj) -> None:
    (tmp / "config.json").write_text(json.dumps(obj), encoding="utf-8")


# --- US1: first-run bootstrap ---

def test_bootstrap_creates_template_when_missing(tmp_cwd):
    assert not (tmp_cwd / "config.json").exists()
    path = config_store.bootstrap()
    assert path == tmp_cwd / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "client_id" in data and "email" in data
    assert "_README" in data and "_help_url" in data


def test_load_missing_raises_not_found(tmp_cwd):
    with pytest.raises(config_store.ConfigNotFound):
        config_store.load()


# --- feature 008: fetch_batch_size (P6) ---

def test_load_fetch_batch_size_default_invalid_and_valid(tmp_cwd):
    base = {"client_id": "abc-123", "email": "me@outlook.com"}
    _write(tmp_cwd, base)
    assert config_store.load().fetch_batch_size == config.FETCH_BATCH_DEFAULT  # 缺漏→預設
    for bad in ("abc", 0, -3, None):
        _write(tmp_cwd, {**base, "fetch_batch_size": bad})
        assert config_store.load().fetch_batch_size == config.FETCH_BATCH_DEFAULT  # 無效→預設、不崩潰
    _write(tmp_cwd, {**base, "fetch_batch_size": 200})
    assert config_store.load().fetch_batch_size == 200  # 正整數生效
    _write(tmp_cwd, {**base, "fetch_batch_size": 1})
    assert config_store.load().fetch_batch_size == 1   # 下限 1


# --- US2: configuration from the working directory ---

def test_load_valid_returns_configuration(tmp_cwd):
    _write(tmp_cwd, {"client_id": "abc-123", "email": "me@outlook.com"})
    cfg = config_store.load()
    assert cfg.client_id == "abc-123"
    assert cfg.email == "me@outlook.com"
    assert cfg.imap_host == config.IMAP_HOST
    assert cfg.imap_port == config.IMAP_PORT
    assert cfg.timeout == config.IMAP_TIMEOUT
    assert cfg.authority == config.AUTHORITY  # fixed, from code
    assert cfg.scopes == config.SCOPES


def test_load_optional_overrides_applied(tmp_cwd):
    _write(
        tmp_cwd,
        {"client_id": "abc", "email": "me@x.com",
         "imap_host": "imap.example.com", "imap_port": 1993, "timeout": 5},
    )
    cfg = config_store.load()
    assert cfg.imap_host == "imap.example.com"
    assert cfg.imap_port == 1993
    assert cfg.timeout == 5


def test_authority_and_scopes_not_taken_from_json(tmp_cwd):
    _write(
        tmp_cwd,
        {"client_id": "abc", "email": "me@x.com",
         "_README": "ignored", "authority": "https://evil", "scopes": ["x"]},
    )
    cfg = config_store.load()
    assert cfg.authority == config.AUTHORITY
    assert cfg.scopes == config.SCOPES


# --- US3: unfilled / placeholder / malformed ---

@pytest.mark.parametrize("bad", ["", "   ", config_store.EMAIL_PLACEHOLDER])
def test_load_unfilled_email_raises(tmp_cwd, bad):
    _write(tmp_cwd, {"client_id": "abc", "email": bad})
    with pytest.raises(config_store.ConfigError) as ei:
        config_store.load()
    assert "email" in str(ei.value)


@pytest.mark.parametrize("bad", ["", config_store.CLIENT_ID_PLACEHOLDER])
def test_load_unfilled_client_id_raises(tmp_cwd, bad):
    _write(tmp_cwd, {"client_id": bad, "email": "me@x.com"})
    with pytest.raises(config_store.ConfigError) as ei:
        config_store.load()
    assert "client_id" in str(ei.value)


def test_load_malformed_json_raises(tmp_cwd):
    (tmp_cwd / "config.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(config_store.ConfigError):
        config_store.load()


# --- US4 support: write-back only the email, never a token (FR-017 / A3) ---

def test_write_email_updates_only_email_atomically(tmp_cwd):
    _write(tmp_cwd, {"client_id": "abc", "email": "old@x.com", "imap_port": 993})
    config_store.write_email("new@x.com")
    data = json.loads((tmp_cwd / "config.json").read_text(encoding="utf-8"))
    assert data["email"] == "new@x.com"
    assert data["client_id"] == "abc"
    assert data["imap_port"] == 993
    assert not any("token" in k.lower() or "secret" in k.lower() for k in data)
