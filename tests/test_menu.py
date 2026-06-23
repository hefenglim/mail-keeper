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


def test_menu_action_error_returns_to_menu_not_exit():
    # A：單一動作丟出預期錯誤（如找不到檔案）→ 回選單、印訊息、不把整個 app 帶走
    def boom():
        raise RuntimeError("無法讀取 CSV inbox")

    msgs: list[str] = []
    reads = iter(["1", "0"])  # 選會出錯的動作 → 再選 0 離開
    menu.run(
        [("boom", boom)],
        read=lambda prompt="": next(reads),
        out=lambda *a, **k: msgs.append(" ".join(str(x) for x in a)),
    )
    # run 正常返回（例外未外傳），且印出了失敗訊息
    assert any("無法讀取 CSV inbox" in m for m in msgs)


def test_main_no_subcommand_non_tty_prints_usage_and_exits():
    # pytest 串流為非 TTY → 無子指令 → 印用法 + 非零結束、不卡死（不呼叫 input）
    with pytest.raises(SystemExit) as ei:
        cli.main([])
    assert ei.value.code != 0
