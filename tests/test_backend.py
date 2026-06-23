"""Foundational backend tests — MailHeader.recipients + folder-name parsing. Test-first."""
from __future__ import annotations

import pytest

from mailkeeper.imap_client import (
    BackendError,
    MailHeader,
    OutlookIMAPClient,
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


# --- D: real batched-FETCH response parsing (offline, injected fake conn) ---

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


class _FakeConn:
    def __init__(self, search_uids: bytes, fetch_results: list) -> None:
        self._search = search_uids
        self._fetch = list(fetch_results)
        self._i = 0

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def uid(self, command, *args):
        if command == "search":
            return ("OK", [self._search])
        if command == "fetch":
            r = self._fetch[self._i]
            self._i += 1
            return r
        raise AssertionError(command)


def _client_with(conn) -> OutlookIMAPClient:
    c = OutlookIMAPClient.__new__(OutlookIMAPClient)
    c._imap = conn  # `_conn` property 讀的就是 `_imap`
    return c


def test_list_headers_parses_batched_fetch_uids():
    msg_data = [
        (b"1 (UID 10 BODY[HEADER.FIELDS (SUBJECT FROM TO DATE)] {20}", b"Subject: A\r\nFrom: a@x\r\n"),
        b")",
        (b"2 (UID 11 BODY[...] {20}", b"Subject: B\r\nFrom: b@x\r\n"),
        b")",
    ]
    client = _client_with(_FakeConn(b"10 11", [("OK", msg_data)]))
    headers = client.list_headers("INBOX")
    assert [h.uid for h in headers] == ["10", "11"]  # UID 正確對位
    assert [h.subject for h in headers] == ["A", "B"]


def test_list_headers_raises_on_batch_failure():
    client = _client_with(_FakeConn(b"10 11", [("NO", None)]))
    with pytest.raises(BackendError):
        client.list_headers("INBOX")  # 批次失敗大聲報錯、不靜默回傳不完整
