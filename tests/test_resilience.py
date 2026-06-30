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


# ── auth._username（純 dict 邏輯，離線可測；SR C1：不可 pragma 藏邏輯）─────────

def test_username_prefers_preferred_username():
    assert auth._username(_FakeApp([], None), {"id_token_claims": {"preferred_username": "a@x.com"}}) == "a@x.com"


def test_username_falls_back_to_email_claim():
    assert auth._username(_FakeApp([], None), {"id_token_claims": {"email": "b@x.com"}}) == "b@x.com"


def test_username_falls_back_to_account():
    assert auth._username(_FakeApp([{"username": "c@x.com"}], None), {}) == "c@x.com"


def test_username_empty_when_no_claims_or_accounts():
    assert auth._username(_FakeApp([], None), {}) == ""


# ── auth.get_access_token（離線：靜默成功 / device-flow 防禦守衛；SR C2）────────
# 真正互動的兩行（印提示 + 阻塞輪詢真實登入）已 pragma；其餘邏輯與兩個 RuntimeError 守衛皆測。

class _FakeDeviceApp:
    def __init__(self, *, accounts=None, silent=None, flow=None, device_result=None):
        self._accounts = accounts or []
        self._silent = silent
        self._flow = flow if flow is not None else {"user_code": "X", "message": "go", "expires_in": 1}
        self._device_result = device_result

    def get_accounts(self): return self._accounts
    def acquire_token_silent(self, scopes, account=None): return self._silent
    def initiate_device_flow(self, scopes=None): return self._flow
    def acquire_token_by_device_flow(self, flow): return self._device_result


def _patch_device(monkeypatch, app):
    monkeypatch.setattr(auth, "_load_cache", lambda p: object())
    monkeypatch.setattr(auth, "_save_cache", lambda c, p: None)
    monkeypatch.setattr(auth.msal, "PublicClientApplication", lambda *a, **k: app)


def test_get_access_token_silent_success(monkeypatch):
    """已有帳號 + 靜默成功 → 直接回 (token, username)，不走 device flow。"""
    app = _FakeDeviceApp(accounts=["acct"], silent={"access_token": "tok", "id_token_claims": {"preferred_username": "u@x.com"}})
    _patch_device(monkeypatch, app)
    assert auth.get_access_token(_cfg()) == ("tok", "u@x.com")


def test_get_access_token_device_flow_not_started_raises(monkeypatch):
    """initiate_device_flow 無 user_code（啟動失敗）→ RuntimeError（防禦守衛）。"""
    _patch_device(monkeypatch, _FakeDeviceApp(accounts=[], flow={}))
    with pytest.raises(RuntimeError):
        auth.get_access_token(_cfg())


def test_get_access_token_device_flow_success(monkeypatch):
    """device flow 完成、取得 token → 回 (token, username)。"""
    app = _FakeDeviceApp(accounts=[], device_result={"access_token": "tok2", "id_token_claims": {"email": "d@x.com"}})
    _patch_device(monkeypatch, app)
    assert auth.get_access_token(_cfg()) == ("tok2", "d@x.com")


def test_get_access_token_no_token_after_device_flow_raises(monkeypatch):
    """device flow 結束卻無 access_token → RuntimeError（防禦守衛）。"""
    _patch_device(monkeypatch, _FakeDeviceApp(accounts=[], device_result={"error": "expired"}))
    with pytest.raises(RuntimeError):
        auth.get_access_token(_cfg())


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
