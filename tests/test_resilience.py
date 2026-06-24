"""R7 韌性基礎：auth 僅靜默續期 + config.json 韌性設定解析（離線）。"""
from __future__ import annotations

import json

import pytest

from mailkeeper import auth, config, config_store
from mailkeeper.imap_client import ReauthRequired


# ── auth.get_token_silent（離線：以假 MSAL app 取代）──────────────────────────

class _FakeApp:
    def __init__(self, accounts, silent_result):
        self._accounts = accounts
        self._silent = silent_result

    def get_accounts(self):
        return self._accounts

    def acquire_token_silent(self, scopes, account=None):
        return self._silent


def _cfg() -> config_store.Configuration:
    return config_store.Configuration(
        client_id="id", email="e@x.com", imap_host="h", imap_port=993, timeout=60,
        authority="auth", scopes=["s"], token_cache_path="x.bin",
    )


def _patch_msal(monkeypatch, accounts, silent):
    monkeypatch.setattr(auth, "_load_cache", lambda p: object())
    monkeypatch.setattr(auth, "_save_cache", lambda c, p: None)
    monkeypatch.setattr(
        auth.msal, "PublicClientApplication", lambda *a, **k: _FakeApp(accounts, silent)
    )


def test_silent_returns_refreshed_token(monkeypatch):
    _patch_msal(monkeypatch, ["acct"], {"access_token": "tok-new"})
    assert auth.get_token_silent(_cfg()) == "tok-new"


def test_silent_no_account_raises_reauth(monkeypatch):
    _patch_msal(monkeypatch, [], None)
    with pytest.raises(ReauthRequired):
        auth.get_token_silent(_cfg())


def test_silent_refresh_failure_raises_reauth(monkeypatch):
    # 有帳號但 refresh 失效（silent 回 None）→ ReauthRequired，絕不退化互動
    _patch_msal(monkeypatch, ["acct"], None)
    with pytest.raises(ReauthRequired):
        auth.get_token_silent(_cfg())


# ── config.json 韌性設定（缺漏→預設、無效→預設、有效→生效）──────────────────

def _write_cfg(tmp_cwd, extra):
    payload = {"client_id": "a", "email": "e@x.com", **extra}
    (tmp_cwd / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_resilience_defaults_when_absent(tmp_cwd):
    _write_cfg(tmp_cwd, {})
    cfg = config_store.load()
    assert cfg.max_consecutive_failures == config.MAX_CONSECUTIVE_FAILURES
    assert cfg.max_reconnect_attempts == config.MAX_RECONNECT_ATTEMPTS
    assert cfg.backoff_cap_seconds == config.BACKOFF_CAP_SECONDS


def test_resilience_invalid_falls_back_to_defaults(tmp_cwd):
    _write_cfg(tmp_cwd, {"max_consecutive_failures": "x", "backoff_base_seconds": -1})
    cfg = config_store.load()
    assert cfg.max_consecutive_failures == config.MAX_CONSECUTIVE_FAILURES
    assert cfg.backoff_base_seconds == config.BACKOFF_BASE_SECONDS  # -1 無效 → 預設


def test_resilience_valid_values_used(tmp_cwd):
    _write_cfg(tmp_cwd, {"max_consecutive_failures": 5, "max_reconnect_attempts": 7})
    cfg = config_store.load()
    assert cfg.max_consecutive_failures == 5 and cfg.max_reconnect_attempts == 7


def test_resilience_cap_below_base_repaired(tmp_cwd):
    # 封頂 < base 為無效組合 → 修正（不崩潰、cap ≥ base）
    _write_cfg(tmp_cwd, {"backoff_base_seconds": 2, "backoff_cap_seconds": 1})
    cfg = config_store.load()
    assert cfg.backoff_cap_seconds >= cfg.backoff_base_seconds
