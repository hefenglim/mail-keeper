"""US3/feature 001 — anticipated failures end cleanly; no traceback, no token. Test-first."""
from __future__ import annotations

import imaplib
import socket

import pytest

from mailkeeper import cli


def _connect_raiser(exc: BaseException):
    def _connect():
        raise exc

    return _connect


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("取得 token 失敗：invalid_grant"),
        imaplib.IMAP4.error("User is authenticated but not connected."),
        socket.timeout("timed out"),
        OSError("network down"),
    ],
)
def test_main_anticipated_failures_exit_clean(monkeypatch, capsys, tmp_path, exc):
    monkeypatch.setattr(cli, "_connect", _connect_raiser(exc))
    with pytest.raises(SystemExit) as ei:
        cli.main(["export-folders", "--out", str(tmp_path / "f.csv")])
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert "MailKeeper" in err
    assert "Traceback" not in err


def test_main_unexpected_error_hides_traceback_and_payload(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_connect", _connect_raiser(ValueError("secret-bearer-AbC123")))
    with pytest.raises(SystemExit) as ei:
        cli.main(["export-folders", "--out", str(tmp_path / "f.csv")])
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "secret-bearer-AbC123" not in err  # never echo payloads/tokens
