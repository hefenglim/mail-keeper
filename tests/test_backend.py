"""Foundational backend tests — pure helpers only.

純函式單元測試（MailHeader / 折疊解析 / 切批 / UID 抽取）。任何需要 IMAP「連線」的測試
一律改用線級引擎（`tests/imap_server.py::ImapServer` + `imap_transport`，真 imaplib over 引擎）。
"""
from __future__ import annotations

import pytest

from mailkeeper.imap_client import (
    MailHeader,
    _chunked,
    _extract_uid,
    _parse_folder_name,
)


def test_mailheader_recipients_appended_last_with_default():
    # 既有 4 位置引數仍對映到 uid/subject/sender/date；recipients 預設 ""
    h = MailHeader("1", "Subj", "from@x.com", "Mon")
    assert (h.uid, h.subject, h.sender, h.date) == ("1", "Subj", "from@x.com", "Mon")
    assert h.recipients == ""


def test_mailheader_recipients_settable():
    h = MailHeader("2", "S", "f@x.com", "Tue", "to@x.com")
    assert h.recipients == "to@x.com"


@pytest.mark.parametrize(
    "line,expected",
    [
        (b'(\\HasNoChildren) "/" "INBOX"', "INBOX"),
        (b'(\\HasNoChildren) "/" INBOX', "INBOX"),
        (b'(\\HasNoChildren) "/" "Work/Projects"', "Work/Projects"),
        (b'(\\HasNoChildren) "/" "Has Space"', "Has Space"),
        # modified UTF-7 (RFC 3501 example: "&U,BTFw-" == 台北)
        (b'(\\HasNoChildren) "/" "&U,BTFw-"', "台北"),
    ],
)
def test_parse_folder_name(line, expected):
    assert _parse_folder_name(line) == expected


# --- US3: batching helper + on_progress contract ---

@pytest.mark.parametrize(
    "seq,size,expected",
    [
        ([1, 2, 3, 4, 5], 2, [[1, 2], [3, 4], [5]]),
        ([1, 2, 3, 4], 2, [[1, 2], [3, 4]]),
        ([], 3, []),
        ([1, 2], 5, [[1, 2]]),
    ],
)
def test_chunked(seq, size, expected):
    assert [list(c) for c in _chunked(seq, size)] == expected


def test_fake_list_headers_reports_progress(folder_backend):
    seen: list[tuple[int, int]] = []
    folder_backend.list_headers("INBOX", on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 2), (2, 2)]  # INBOX 有 2 封 → 逐封回報 done/total


def test_fake_list_uids_returns_set_and_reports_progress(folder_backend):
    # feature 006：FakeBackend.list_uids 回 UID 集合並驅動 determinate 進度
    seen: list[tuple[int, int]] = []
    uids = folder_backend.list_uids("INBOX", on_progress=lambda d, t: seen.append((d, t)))
    assert uids == {"10", "11"}      # INBOX 兩封
    assert seen == [(1, 2), (2, 2)]  # 逐筆推進至 total


# --- UID 抽取（純函式）。list_headers 解析的整合測試見 test_imap_contract.py ---

@pytest.mark.parametrize(
    "meta,expected",
    [
        (b"1 (UID 10 BODY[HEADER.FIELDS (SUBJECT)] {88}", "10"),
        (b"426 (UID 113164 BODY[...] {50}", "113164"),  # 開頭 426 是序號、UID 才是 113164
        (b"1 (BODY[...] {88}", ""),  # 無 UID token → ""
        (b")", ""),
        (None, ""),
    ],
)
def test_extract_uid(meta, expected):
    assert _extract_uid(meta) == expected
