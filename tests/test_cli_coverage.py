"""群 2 覆蓋補強 —— cli.py 的有意義邏輯/防禦分支（注入 backend + 假 stdin/argv，離線）。

涵蓋：classify 無候選/互動確認/ReauthRequired 乾淨停止、報告列出不可行列、互動選單三動作與
無效選擇防呆、main 子指令分派與互動選單入口。純 input 包裝（_prompt_choice/_prompt_yes_no）、
狀態提示 _emit_status、`__main__` 入口與 auth/MSAL 屬「純 I/O/入口」，另以 `# pragma: no cover`
誠實標記，不在此測。
"""
from __future__ import annotations

import contextlib

import pytest

from conftest import FakeBackend

from mailkeeper import cli
from mailkeeper.domain import MailHeader, ReauthRequired

WS_HEADER = "uid,current_folder,target_folder,date,from,to,subject\n"


def _ws(tmp_path, *rows: str):
    """寫一份工作表 CSV，回傳路徑字串。"""
    p = tmp_path / "w.csv"
    p.write_text(WS_HEADER + "".join(r + "\n" for r in rows), encoding="utf-8-sig")
    return str(p)


def _seq_input(monkeypatch, *vals):
    it = iter(vals)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


class _ReauthOnMove(FakeBackend):
    """move_many 擲 ReauthRequired —— 驗 classify 執行中需重新登入時乾淨停止（235-243）。"""
    def move_many(self, uids, dest_folder, mailbox="INBOX"):
        raise ReauthRequired("relogin")


# ── classify：無候選 / 不可行列報告 / 互動確認 / ReauthRequired ────────────────

def test_classify_no_candidates_prints_and_returns(tmp_path, capsys):
    """全為 SKIP（target 留空）→ 無候選 → 印訊息後返回（209-210）。"""
    backend = FakeBackend(folders={"INBOX": [MailHeader("1", "S", "a", "Mon")], "Archive": []})
    cli.classify(backend, _ws(tmp_path, "1,INBOX,"), run=False, interactive=False)
    assert "沒有需要搬移" in capsys.readouterr().out


def test_classify_reports_infeasible_rows(tmp_path, capsys):
    """來源夾不存在 → 不可行列 → 報告經 stderr 逐列列出（_print_report，184）。"""
    backend = FakeBackend(folders={"INBOX": [], "Archive": []})
    cli.classify(backend, _ws(tmp_path, "5,Nope,Archive"), run=False, interactive=False)
    err = capsys.readouterr().err
    assert "不可行" in err and "Nope" in err


def test_classify_interactive_confirm_then_moves(tmp_path):
    """非 --run 且互動 → 詢問確認（214）；ask 回 True → 實際搬移。"""
    backend = FakeBackend(folders={"INBOX": [MailHeader("1", "S", "a", "Mon")], "Archive": []})
    cli.classify(backend, _ws(tmp_path, "1,INBOX,Archive"), run=False, interactive=True, ask=lambda: True)
    assert ("move", "1", "Archive", "INBOX") in backend.actions


def test_classify_reauth_required_stops_cleanly(tmp_path, capsys):
    """執行中 move_many 擲 ReauthRequired → 乾淨停止、回報已/未完成 + 重新登入指引（235-243）。"""
    backend = _ReauthOnMove(folders={"INBOX": [MailHeader("1", "S", "a", "Mon")], "Archive": []})
    cli.classify(backend, _ws(tmp_path, "1,INBOX,Archive"), run=True, interactive=False)
    assert "重新登入" in capsys.readouterr().err


# ── 互動選單三動作 + 無效選擇防呆（265-274, 278-279, 283-284, 293）─────────────

def test_menu_export_worksheet_valid_selection(tmp_path, monkeypatch):
    backend = FakeBackend(folders={"INBOX": [MailHeader("1", "S", "a", "Mon", "b")], "Archive": []})
    out = str(tmp_path / "w.csv")
    _seq_input(monkeypatch, "1", out)  # 選第 1 個夾、輸出路徑
    cli._menu_export_worksheet(backend)
    assert (tmp_path / "w.csv").exists()


def test_menu_export_worksheet_invalid_selection(tmp_path, monkeypatch, capsys):
    backend = FakeBackend(folders={"INBOX": [], "Archive": []})
    _seq_input(monkeypatch, "99")  # 超出範圍 → 防呆
    cli._menu_export_worksheet(backend)
    assert "無效的資料夾選擇" in capsys.readouterr().err


def test_menu_export_folders(tmp_path, monkeypatch):
    backend = FakeBackend(folders={"INBOX": [], "Archive": []})
    out = str(tmp_path / "f.csv")
    _seq_input(monkeypatch, out)
    cli._menu_export_folders(backend)
    assert (tmp_path / "f.csv").exists()


def test_menu_classify(tmp_path, monkeypatch, capsys):
    backend = FakeBackend(folders={"INBOX": [MailHeader("1", "S", "a", "Mon")], "Archive": []})
    csv_path = _ws(tmp_path, "1,INBOX,")  # 全 SKIP → classify 早返回，不需確認
    _seq_input(monkeypatch, csv_path)
    cli._menu_classify(backend)
    assert "沒有需要搬移" in capsys.readouterr().out


def test_menu_options_wires_three_actions():
    backend = FakeBackend(folders={"INBOX": []})
    opts = cli._menu_options(backend)  # 293：組裝 (標籤, 動作) 清單
    assert len(opts) == 3 and all(callable(fn) for _, fn in opts)


# ── main：子指令分派 + 互動選單入口（323-324, 334-335）────────────────────────

def _patch_connect(monkeypatch, backend):
    @contextlib.contextmanager
    def _fake():
        yield backend
    monkeypatch.setattr(cli, "_connect", _fake)
    monkeypatch.setattr(cli.console, "setup", lambda: None)  # 不包裹 stdout，免擾 capsys


def test_main_export_worksheet_subcommand(tmp_path, monkeypatch):
    backend = FakeBackend(folders={"INBOX": [MailHeader("1", "S", "a", "Mon", "b")], "Archive": []})
    _patch_connect(monkeypatch, backend)
    out = str(tmp_path / "w.csv")
    cli.main(["export-worksheet", "--folder", "INBOX", "--out", out])  # 322-324
    assert (tmp_path / "w.csv").exists()


def test_main_no_subcommand_interactive_menu(monkeypatch):
    backend = FakeBackend(folders={"INBOX": []})
    _patch_connect(monkeypatch, backend)

    class _TTY:
        def isatty(self): return True

    monkeypatch.setattr(cli.sys, "stdin", _TTY())
    monkeypatch.setattr(cli.sys, "stdout", _TTY())
    called = {"n": 0}
    # stub menu.run（其內部行為由 test_menu 覆蓋）；只驗 main 走到「互動選單入口」這兩行
    monkeypatch.setattr(cli.menu, "run", lambda options, header=None: called.__setitem__("n", len(options)))
    cli.main([])  # 332 else → 333 isatty → 334-335 menu.run
    assert called["n"] == 3
