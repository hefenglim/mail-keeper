"""US1/US2/US3 cli subcommands + non-TTY + path errors. Test-first."""
from __future__ import annotations

import contextlib

import pytest

from mailkeeper import cli


@contextlib.contextmanager
def _fake_connect(backend):
    yield backend


# --- US1: export-worksheet ---

def test_export_worksheet_writes_english_header(folder_backend, tmp_path):
    out = tmp_path / "w.csv"
    cli.export_worksheet(folder_backend, "INBOX", str(out))
    assert out.read_text(encoding="utf-8-sig").splitlines()[0] == (
        "uid,current_folder,target_folder,date,from,to,subject"
    )


# --- US2: export-folders ---

def test_export_folders_writes(folder_backend, tmp_path):
    out = tmp_path / "f.csv"
    cli.export_folders(folder_backend, str(out))
    lines = out.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "folder"
    assert "Work" in lines and "INBOX" in lines


# --- US3: classify dry-run vs run ---

def test_classify_default_dry_run_no_move(folder_backend, tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("uid,current_folder,target_folder\n10,INBOX,Work\n", encoding="utf-8")
    cli.classify(folder_backend, str(p), run=False, interactive=False)
    assert not any(a[0] == "move" for a in folder_backend.actions)


def test_classify_run_executes(folder_backend, tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("uid,current_folder,target_folder\n10,INBOX,Work\n", encoding="utf-8")
    cli.classify(folder_backend, str(p), run=True, interactive=False)
    assert ("move", "10", "Work", "INBOX") in folder_backend.actions


# --- US2: filename auto-appends .csv (write + read paths) ---

def test_export_worksheet_appends_csv_suffix(folder_backend, tmp_path, capsys):
    cli.export_worksheet(folder_backend, "INBOX", str(tmp_path / "inbox"))
    assert (tmp_path / "inbox.csv").exists()
    assert not (tmp_path / "inbox").exists()
    assert "inbox.csv" in capsys.readouterr().out  # 確認訊息顯示實際檔名 (FR-006)


def test_export_folders_appends_csv_suffix(folder_backend, tmp_path):
    cli.export_folders(folder_backend, str(tmp_path / "folders"))
    assert (tmp_path / "folders.csv").exists()


def test_export_keeps_non_csv_extension(folder_backend, tmp_path):
    cli.export_folders(folder_backend, str(tmp_path / "list.txt"))
    assert (tmp_path / "list.txt").exists()  # 既有副檔名保留、不改成 .csv


def test_classify_appends_csv_suffix_on_read(folder_backend, tmp_path, capsys):
    (tmp_path / "inbox.csv").write_text(
        "uid,current_folder,target_folder\n10,INBOX,Work\n", encoding="utf-8-sig"
    )
    # 給無副檔名 "inbox" → 應解析為 inbox.csv 並成功讀取（不丟 CsvError）
    cli.classify(folder_backend, str(tmp_path / "inbox"), run=False, interactive=False)
    assert "Work" in capsys.readouterr().out  # 報告讀到候選 10→Work（US2 場景3）


# --- T019/T021: subcommand runs under non-TTY without blocking on input() ---

def test_main_subcommand_runs_under_non_tty(folder_backend, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_connect", lambda: _fake_connect(folder_backend))
    cli.main(["export-folders", "--out", str(tmp_path / "f.csv")])
    assert (tmp_path / "f.csv").exists()


# --- T028: bad path → clean non-zero error, no traceback ---

def test_main_classify_missing_file_clean_error(folder_backend, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_connect", lambda: _fake_connect(folder_backend))
    with pytest.raises(SystemExit) as ei:
        cli.main(["classify", "--in", str(tmp_path / "does-not-exist.csv")])
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert "Traceback" not in err


# --- US3: cli wires progress; non-TTY no contamination ---

def test_export_worksheet_wires_progress(folder_backend, tmp_path, monkeypatch):
    captured: dict = {}
    real = folder_backend.list_headers

    def spy(folder, *, on_progress=None):
        captured["on_progress"] = on_progress
        return real(folder, on_progress=on_progress)

    monkeypatch.setattr(folder_backend, "list_headers", spy)
    cli.export_worksheet(folder_backend, "INBOX", str(tmp_path / "w.csv"))
    assert captured["on_progress"] is not None  # cli 已把進度回呼接上 list_headers


def test_cli_non_tty_no_progress_contamination(folder_backend, tmp_path, capsys):
    cli.export_worksheet(folder_backend, "INBOX", str(tmp_path / "w.csv"))
    p = tmp_path / "x.csv"
    p.write_text("uid,current_folder,target_folder\n10,INBOX,Work\n", encoding="utf-8-sig")
    cli.classify(folder_backend, str(p), run=True, interactive=False)
    cap = capsys.readouterr()
    assert "\r" not in cap.out and "\r" not in cap.err  # 非 TTY 無進度控制字元污染
    assert (tmp_path / "w.csv").exists()
    assert ("move", "10", "Work", "INBOX") in folder_backend.actions


# --- E: report lists folders that will be created ---

def test_classify_report_lists_new_folders(folder_backend, tmp_path, capsys):
    p = tmp_path / "w.csv"
    p.write_text("uid,current_folder,target_folder\n10,INBOX,BrandNew\n", encoding="utf-8-sig")
    cli.classify(folder_backend, str(p), run=False, interactive=False)
    out = capsys.readouterr().out
    assert "將新建" in out and "BrandNew" in out  # 確認前列出將被新建的資料夾


# --- C: cli warns + reports remaining when execute stops early ---

def test_classify_warns_when_execution_stops_early(folder_backend, tmp_path, capsys, monkeypatch):
    from mailkeeper import classifier

    p = tmp_path / "w.csv"
    p.write_text(
        "uid,current_folder,target_folder\n10,INBOX,Work\n11,INBOX,Work\n", encoding="utf-8-sig"
    )
    # 模擬 execute 因連線中斷提前停止：候選 2 筆、只回 1 筆
    monkeypatch.setattr(
        classifier,
        "execute",
        lambda backend, items, *, on_progress=None: [
            classifier.MoveResult(classifier.candidates(items)[0].row, False, "EOF")
        ],
    )
    cli.classify(folder_backend, str(p), run=True, interactive=False)
    err = capsys.readouterr().err
    assert "剩餘 1" in err and "連線中斷" in err
