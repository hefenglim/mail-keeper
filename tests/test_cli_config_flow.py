"""US1/US2 wiring — cli._run integrates config load, bootstrap, identity check. Test-first.

連線一律走真實 OutlookIMAPClient + FakeIMAPConn（install），不再用任意假 client 替身。
"""
from __future__ import annotations

import json

import pytest

from imap_dataset import fresh_sim
from imap_sim import install

from mailkeeper import cli, config_store


def test_run_missing_config_bootstraps_then_exits_without_auth(tmp_cwd, monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        cli, "get_access_token", lambda cfg: called.append("auth") or ("t", "e@x.com")
    )
    with pytest.raises(SystemExit) as ei:
        cli._run()
    assert ei.value.code != 0
    assert (tmp_cwd / "config.json").exists()  # template created
    assert called == []  # never attempted to authenticate


def test_run_valid_config_uses_configured_email_and_timeout(tmp_cwd, monkeypatch):
    (tmp_cwd / "config.json").write_text(
        json.dumps({"client_id": "abc", "email": "me@x.com", "timeout": 42}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "get_access_token", lambda cfg: ("tok", "me@x.com"))
    # 從母版出發：INBOX 有多封（含 newsletter），run_listing/organizer 規則迴圈確實執行
    sim = fresh_sim()
    cap = install(monkeypatch, sim)

    cli._run()  # identity matches → no prompt；真實 client 跑在模擬器上

    assert cap["timeout"] == 42  # 設定的 timeout 確實傳到 IMAP4_SSL
    # XOAUTH2 認證字串帶設定的 email 與 token
    assert sim.auth_string == b"user=me@x.com\x01auth=Bearer tok\x01\x01"


def test_run_mismatch_non_interactive_aborts(tmp_cwd, monkeypatch):
    # configured email differs from the authenticated identity; pytest streams are
    # non-tty -> interactive is False -> _run must abort safely before connecting.
    (tmp_cwd / "config.json").write_text(
        json.dumps({"client_id": "abc", "email": "configured@x.com"}), encoding="utf-8"
    )
    monkeypatch.setattr(cli, "get_access_token", lambda cfg: ("tok", "different@x.com"))
    cap = install(monkeypatch, fresh_sim())

    with pytest.raises(config_store.ConfigError):
        cli._run()

    assert cap["constructed"] == 0  # 中止於連線之前，IMAP4_SSL 從未被建構
