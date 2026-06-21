"""US4 — account-mismatch verification. Test-first."""
from __future__ import annotations

import pytest

from mailkeeper import cli, config_store


def test_match_is_silent_and_not_asked():
    asked = []
    result = cli.verify_account(
        "a@x.com", "A@X.com",  # same address, different case
        interactive=True,
        ask=lambda: asked.append("x") or cli.CHOICE_KEEP,
        write_back=lambda e: None,
    )
    assert result == "a@x.com"
    assert asked == []  # no prompt when they match


def test_choice_use_and_write_returns_auth_and_persists():
    written: list[str] = []
    result = cli.verify_account(
        "old@x.com", "new@x.com",
        interactive=True,
        ask=lambda: cli.CHOICE_USE_WRITE,
        write_back=written.append,
    )
    assert result == "new@x.com"
    assert written == ["new@x.com"]


def test_choice_use_once_returns_auth_without_write():
    written: list[str] = []
    result = cli.verify_account(
        "old@x.com", "new@x.com",
        interactive=True,
        ask=lambda: cli.CHOICE_USE_ONCE,
        write_back=written.append,
    )
    assert result == "new@x.com"
    assert written == []


def test_choice_keep_returns_configured():
    result = cli.verify_account(
        "old@x.com", "new@x.com",
        interactive=True,
        ask=lambda: cli.CHOICE_KEEP,
        write_back=lambda e: None,
    )
    assert result == "old@x.com"


def test_choice_abort_raises():
    with pytest.raises(config_store.ConfigError):
        cli.verify_account(
            "old@x.com", "new@x.com",
            interactive=True,
            ask=lambda: cli.CHOICE_ABORT,
            write_back=lambda e: None,
        )


def test_non_interactive_mismatch_aborts_safely():
    with pytest.raises(config_store.ConfigError) as ei:
        cli.verify_account(
            "old@x.com", "new@x.com",
            interactive=False,
            ask=lambda: cli.CHOICE_USE_WRITE,
            write_back=lambda e: None,
        )
    assert "非互動" in str(ei.value)
