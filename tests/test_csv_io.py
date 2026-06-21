"""US1/US2/US3 — CSV write/read. Test-first."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mailkeeper import csv_io
from mailkeeper.imap_client import MailHeader

HEADER = ["uid", "current_folder", "target_folder", "date", "from", "to", "subject"]


def _rows(path) -> list[list[str]]:
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


# --- US1: write_worksheet ---

def test_write_worksheet_fixed_header_and_blank_target(tmp_path):
    headers = [MailHeader("1", "Subj", "a@x.com", "Mon", "b@x.com")]
    out = tmp_path / "w.csv"
    csv_io.write_worksheet(headers, "INBOX", out)
    rows = _rows(out)
    assert rows[0] == HEADER
    assert rows[1] == ["1", "INBOX", "", "Mon", "a@x.com", "b@x.com", "Subj"]


def test_write_worksheet_escapes_special_chars(tmp_path):
    headers = [MailHeader("2", 'has, "quote" and\nnewline', "a@x.com", "Mon", "b@x.com")]
    out = tmp_path / "w.csv"
    csv_io.write_worksheet(headers, "INBOX", out)
    assert _rows(out)[1][6] == 'has, "quote" and\nnewline'  # csv round-trip


def test_write_worksheet_multilingual(tmp_path):
    samples = ["中文標題", "English", "日本語テスト", "한국어", "العربية", "emoji 🎉"]
    headers = [MailHeader(str(i), s, "a@x.com", "Mon", "b@x.com") for i, s in enumerate(samples)]
    out = tmp_path / "w.csv"
    csv_io.write_worksheet(headers, "INBOX", out)
    text = out.read_text(encoding="utf-8")
    for s in samples:
        assert s in text  # ≥5 語文 + emoji 保留 (SC-002)


# --- US2: write_folders ---

def test_write_folders_folder_only(tmp_path):
    out = tmp_path / "f.csv"
    csv_io.write_folders(["INBOX", "Work/Projects", "封存"], out)
    rows = _rows(out)
    assert rows[0] == ["folder"]
    assert rows[1:] == [["INBOX"], ["Work/Projects"], ["封存"]]


# --- US3: read_worksheet ---

def test_read_worksheet_parses_and_tolerates_extra(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text(
        "uid,current_folder,target_folder,date,from,to,subject,extra\n"
        "1,INBOX,Work,Mon,a,b,S,xx\n",
        encoding="utf-8",
    )
    rows = csv_io.read_worksheet(p)
    assert len(rows) == 1
    assert (rows[0].uid, rows[0].current_folder, rows[0].target_folder) == ("1", "INBOX", "Work")


def test_read_worksheet_missing_required_col_raises(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("uid,current_folder,date\n1,INBOX,Mon\n", encoding="utf-8")
    with pytest.raises(csv_io.CsvError):
        csv_io.read_worksheet(p)


def test_read_worksheet_no_header_raises(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    with pytest.raises(csv_io.CsvError):
        csv_io.read_worksheet(p)


# --- T027: overwrite existing ---

def test_write_worksheet_overwrites_existing(tmp_path):
    out = tmp_path / "w.csv"
    out.write_text("OLD-WORKSHEET", encoding="utf-8")
    csv_io.write_worksheet([MailHeader("1", "S", "a", "Mon", "b")], "INBOX", out)
    assert "OLD-WORKSHEET" not in out.read_text(encoding="utf-8")


def test_write_folders_overwrites_existing(tmp_path):
    out = tmp_path / "f.csv"
    out.write_text("OLD-FOLDERS", encoding="utf-8")
    csv_io.write_folders(["INBOX"], out)
    assert "OLD-FOLDERS" not in out.read_text(encoding="utf-8")


# --- T026: no secret in output ---

def test_worksheet_output_has_no_token(tmp_path):
    out = tmp_path / "w.csv"
    csv_io.write_worksheet([MailHeader("1", "Subj", "a@x.com", "Mon", "b@x.com")], "INBOX", out)
    text = out.read_text(encoding="utf-8").lower()
    assert "bearer" not in text and "token" not in text
