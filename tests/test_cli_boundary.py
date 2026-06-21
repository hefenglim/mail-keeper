"""US3 — anticipated failures end cleanly; no traceback, no token. Test-first."""
from __future__ import annotations

import imaplib
import socket

import pytest

from mailkeeper import cli


def _raiser(exc: BaseException):
    def _run() -> None:
        raise exc

    return _run


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("取得 token 失敗：invalid_grant"),
        imaplib.IMAP4.error("User is authenticated but not connected."),
        socket.timeout("timed out"),
        OSError("network down"),
    ],
)
def test_main_anticipated_failures_exit_clean(monkeypatch, capsys, exc):
    monkeypatch.setattr(cli, "_run", _raiser(exc))
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert "MailKeeper" in err
    assert "Traceback" not in err


def test_main_unexpected_error_hides_traceback_and_payload(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_run", _raiser(ValueError("secret-bearer-AbC123")))
    with pytest.raises(SystemExit) as ei:
        cli.main()
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "secret-bearer-AbC123" not in err  # never echo payloads/tokens
