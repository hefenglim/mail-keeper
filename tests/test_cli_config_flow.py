"""US1/US2 wiring — cli._run integrates config load, bootstrap, identity check. Test-first."""
from __future__ import annotations

import json

import pytest

from mailkeeper import cli, config_store


class _FakeClientCtx:
    def __init__(self, backend):
        self._b = backend

    def __enter__(self):
        return self._b

    def __exit__(self, *exc):
        return False


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


def test_run_valid_config_uses_configured_email_and_timeout(tmp_cwd, monkeypatch, make_backend):
    (tmp_cwd / "config.json").write_text(
        json.dumps({"client_id": "abc", "email": "me@x.com", "timeout": 42}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "get_access_token", lambda cfg: ("tok", "me@x.com"))
    backend = make_backend()
    captured: dict = {}

    def fake_client(email, token, **kw):
        captured["email"] = email
        captured["kw"] = kw
        return _FakeClientCtx(backend)

    monkeypatch.setattr(cli, "OutlookIMAPClient", fake_client)

    cli._run()  # must not raise (identity matches → no prompt)

    assert captured["email"] == "me@x.com"
    assert captured["kw"]["timeout"] == 42


def test_run_mismatch_non_interactive_aborts(tmp_cwd, monkeypatch):
    # configured email differs from the authenticated identity; pytest streams are
    # non-tty -> interactive is False -> _run must abort safely before connecting.
    (tmp_cwd / "config.json").write_text(
        json.dumps({"client_id": "abc", "email": "configured@x.com"}), encoding="utf-8"
    )
    monkeypatch.setattr(cli, "get_access_token", lambda cfg: ("tok", "different@x.com"))
    connected: list = []
    monkeypatch.setattr(cli, "OutlookIMAPClient", lambda *a, **k: connected.append(a))

    with pytest.raises(config_store.ConfigError):
        cli._run()

    assert connected == []  # never reached the IMAP connection
