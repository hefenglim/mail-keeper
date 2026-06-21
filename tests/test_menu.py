"""Menu routing + non-interactive safety. Test-first."""
from __future__ import annotations

import pytest

from mailkeeper import cli, menu


def test_menu_routes_to_selected_then_exits():
    called: list[str] = []
    options = [("a", lambda: called.append("a")), ("b", lambda: called.append("b"))]
    reads = iter(["1", "2", "0"])
    menu.run(options, read=lambda prompt="": next(reads), out=lambda *a, **k: None)
    assert called == ["a", "b"]


def test_menu_invalid_then_exit():
    called: list[str] = []
    options = [("a", lambda: called.append("a"))]
    reads = iter(["9", "x", ""])
    menu.run(options, read=lambda prompt="": next(reads), out=lambda *a, **k: None)
    assert called == []


def test_main_no_subcommand_non_tty_prints_usage_and_exits():
    # pytest 串流為非 TTY → 無子指令 → 印用法 + 非零結束、不卡死（不呼叫 input）
    with pytest.raises(SystemExit) as ei:
        cli.main([])
    assert ei.value.code != 0
