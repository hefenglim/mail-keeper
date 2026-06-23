"""保真度測試：FakeIMAPConn 的回應結構必須與『真正的 imaplib 解析器』逐位元組相同。

做法：手寫符合 RFC 3501 的原始 wire bytes（真實伺服器會送的位元組），餵進真 imaplib
（tests/imaplib_probe.py）取得權威解析結果，再要求模擬器對同一邏輯操作產生**完全相同**的
(typ, data)。這是整個離線測試地基的保真度根基——模擬器一旦偏離 imaplib，這裡立刻紅燈。
"""
from __future__ import annotations

import base64

import imaplib_probe as probe
from imap_sim import FakeIMAPConn, client_on, message

from mailkeeper.imap_client import _parse_folder_name


# ── FETCH（帶 literal）逐位元組對拍 ─────────────────────────────────────────

def test_fetch_single_matches_real_imaplib():
    hdr = b"Subject: Hello\r\nFrom: a@x.com\r\nTo: me@x.com\r\nDate: Mon, 1 Jan 2026\r\n\r\n"
    wire = b"* 1 FETCH (UID 10 BODY[HEADER.FIELDS (SUBJECT FROM TO DATE)] {%d}\r\n%s)\r\n" % (
        len(hdr),
        hdr,
    )
    items = "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])"
    real = probe.real_uid_fetch(wire, "10", items)

    sim = FakeIMAPConn({"INBOX": [message(10, "Hello", "a@x.com", "me@x.com", "Mon, 1 Jan 2026")]})
    sim.select("INBOX", readonly=True)
    got = sim.uid("fetch", "10", items)

    assert got == real  # 逐位元組相同（typ + data 結構 + metadata + literal + b')'）


def test_fetch_multiple_matches_real_imaplib():
    h1 = b"Subject: A\r\n\r\n"
    h2 = b"Subject: B\r\n\r\n"
    wire = (
        b"* 1 FETCH (UID 10 BODY[HEADER.FIELDS (SUBJECT)] {%d}\r\n%s)\r\n" % (len(h1), h1)
        + b"* 2 FETCH (UID 11 BODY[HEADER.FIELDS (SUBJECT)] {%d}\r\n%s)\r\n" % (len(h2), h2)
    )
    items = "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
    real = probe.real_uid_fetch(wire, "10,11", items)

    sim = FakeIMAPConn({"INBOX": [message(10, "A"), message(11, "B")]})
    sim.select("INBOX", readonly=True)
    assert sim.uid("fetch", "10,11", items) == real


def test_fetch_cjk_encoded_word_matches_real_imaplib():
    ew = "=?UTF-8?B?" + base64.b64encode("週報".encode()).decode() + "?="
    hdr = f"Subject: {ew}\r\nFrom: a@x.com\r\n\r\n".encode()
    wire = b"* 1 FETCH (UID 10 BODY[HEADER.FIELDS (SUBJECT FROM)] {%d}\r\n%s)\r\n" % (len(hdr), hdr)
    items = "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])"
    real = probe.real_uid_fetch(wire, "10", items)

    sim = FakeIMAPConn({"INBOX": [message(10, "週報", "a@x.com")]})
    sim.select("INBOX", readonly=True)
    assert sim.uid("fetch", "10", items) == real  # CJK 表頭以 encoded-word 編碼、長度一致


# ── SEARCH ─────────────────────────────────────────────────────────────────

def test_search_matches_real_imaplib():
    real = probe.real_uid_search(b"* SEARCH 10 11 12\r\n")
    sim = FakeIMAPConn({"INBOX": [message(10), message(11), message(12)]})
    sim.select("INBOX", readonly=True)
    assert sim.uid("search", None, "ALL") == real


# ── LIST（含 CJK modified-UTF-7）───────────────────────────────────────────

def test_list_matches_real_imaplib_including_mutf7():
    wire = (
        b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n'
        b'* LIST (\\HasNoChildren) "/" "&U,BTFw-"\r\n'  # &U,BTFw- == 台北
    )
    real = probe.real_list(wire)
    sim = FakeIMAPConn({"INBOX": [], "台北": []})
    assert sim.list() == real  # CJK 夾名須為 modified-UTF-7，與真實一致


def test_list_cjk_roundtrips_through_product_decoder():
    # 模擬器 mutf7 編碼 ↔ 產品 _decode_mutf7 解碼，端到端還原 CJK 夾名
    sim = FakeIMAPConn({"台北": [], "Work/Projects": []})
    line = sim.list()[1][0]
    assert _parse_folder_name(line) == "台北"
    assert client_on(sim).list_folders() == ["台北", "Work/Projects"]
