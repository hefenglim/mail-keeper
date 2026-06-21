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
    assert out.read_text(encoding="utf-8").splitlines()[0] == (
        "uid,current_folder,target_folder,date,from,to,subject"
    )


# --- US2: export-folders ---

def test_export_folders_writes(folder_backend, tmp_path):
    out = tmp_path / "f.csv"
    cli.export_folders(folder_backend, str(out))
    lines = out.read_text(encoding="utf-8").splitlines()
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
