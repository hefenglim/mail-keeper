"""IMAP 模擬器引擎 —— 第二批擴充（P1–P11，SR 腦力激盪項目）驗收。

全部跑**真 imaplib over 引擎**。涵蓋：
  * P1 TCP 分段讀取（chunk_size=1 下整流程/內文 round-trip 正確）
  * P2 `* n EXISTS` 成長通知
  * P3 session 中途 UIDVALIDITY 變更（+ 重配 UID 失效）
  * P5 SEARCH 真條件（UNSEEN/SEEN/DELETED/FLAGGED/FROM/SUBJECT/UID）
  * P6 APPEND 同步 literal（新增郵件、配新 UID、可再取回）
  * P7 範圍 FETCH `BODY[]<offset.length>`
  * P8 超大 literal（~1MB）round-trip
  * P9 greeting 變體（PREAUTH / 無 CAPABILITY）
  * P10 畸形 tagged 行 → 受控 abort（協定健壯、不靜默誤判）
  * P11 STATUS / NAMESPACE / LSUB / CONDSTORE（HIGHESTMODSEQ + FETCH MODSEQ）
"""
from __future__ import annotations

import imaplib

import pytest

from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_NEWSLETTER_UID,
    INBOX_SEEN_UID,
    INBOX_USER_DELETED_UID,
    bulk_server,
    fresh_server,
    mime_mailboxes,
    MIME_PLAIN_UID,
)
from imap_server import ImapServer
from imap_sim import FLAGGED, message, mime_message
from imap_transport import SimIMAP4_SSL, connected_client


def _imap_over(server: ImapServer, *, chunk_size=None) -> SimIMAP4_SSL:
    m = SimIMAP4_SSL(server, chunk_size=chunk_size)
    m.authenticate("XOAUTH2", lambda _c: b"user=u\x01auth=Bearer t\x01\x01")
    return m


# ── P1：TCP 分段讀取 ─────────────────────────────────────────────────────────

def test_p1_chunked_delivery_multibatch_headers_correct(monkeypatch):
    # chunk_size=1：伺服器位元組逐一交付，傳輸層須正確重組行/literal；120 封多批仍完整
    server = bulk_server(120)
    headers = connected_client(monkeypatch, server, chunk_size=1).list_headers("INBOX")
    assert len(headers) == 120 and all(h.uid for h in headers)
    assert any(h.subject == "批量信件 CJK" for h in headers)  # 分段下 encoded-word 仍正確


def test_p1_chunked_delivery_mid_literal_body_roundtrips():
    # 內文 literal 跨多次 read（chunk=1）仍精確 — 驗證 mid-literal 重組
    import email
    import email.policy

    server = ImapServer(mime_mailboxes())
    m = _imap_over(server, chunk_size=1)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_PLAIN_UID), "(UID BODY.PEEK[])")
    assert typ == "OK"
    msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
    assert msg["Subject"] == "Plain note" and "Hello world." in msg.get_content()


# ── P2：* n EXISTS 成長通知 ──────────────────────────────────────────────────

def test_p2_exists_growth_notification_recorded():
    server = ImapServer({"INBOX": [message(10), message(20)]})
    server.arm_exists(5, before_op="NOOP")          # 信箱「成長到 5 封」的非請求通知
    m = _imap_over(server)
    m.select("INBOX")
    m.noop()
    # SELECT 先報 * 2 EXISTS；NOOP 期間推送的成長通知 * 5 EXISTS 累積於後
    assert b"5" in m.untagged_responses.get("EXISTS")
    assert any(fe["kind"] == "unsolicited" for fe in server.fault_events)


# ── P3：session 中途 UIDVALIDITY 變更 ────────────────────────────────────────

def test_p3_uidvalidity_change_reported_on_reselect():
    server = fresh_server()
    m = _imap_over(server)
    m.select("INBOX")
    first = m.untagged_responses.get("UIDVALIDITY")
    server.set_uidvalidity("INBOX", 5000)           # session 中途變更
    m.select("INBOX")                               # 重新選取
    assert m.untagged_responses.get("UIDVALIDITY") == [b"5000"]
    assert first != [b"5000"]


def test_p3_uidvalidity_reassign_invalidates_old_uids():
    server = fresh_server()
    old = {m.uid for m in server.mailboxes["INBOX"]}
    server.set_uidvalidity("INBOX", 5000, reassign_uids=True)  # 信箱重建：舊 UID 全失效
    new = {m.uid for m in server.mailboxes["INBOX"]}
    assert old.isdisjoint(new)                      # 舊 UID 不再有效（用過時 UID 操作會指向錯誤郵件）


# ── P5：SEARCH 真條件 ────────────────────────────────────────────────────────

def test_p5_search_criteria_filter_correctly():
    m = _imap_over(fresh_server())
    m.select("INBOX")

    def uids(crit):
        return set(m.uid("search", None, crit)[1][0].split())

    all_uids = {str(u).encode() for u in range(101, 109)}
    assert uids("ALL") == all_uids
    assert uids("SEEN") == {str(INBOX_SEEN_UID).encode()}                 # 只有 105 已讀
    assert uids("UNSEEN") == all_uids - {str(INBOX_SEEN_UID).encode()}
    assert uids("DELETED") == {str(INBOX_USER_DELETED_UID).encode()}      # 只有 106 已標刪
    assert uids('FROM "boss"') == {str(INBOX_CJK_UID).encode()}           # 102: 王經理 <boss@x.com>
    assert uids('SUBJECT "Newsletter"') == {str(INBOX_NEWSLETTER_UID).encode()}  # 101: Weekly Newsletter
    assert uids("UID 101,102") == {b"101", b"102"}
    # 註：CJK 子字串搜尋（如 SUBJECT "報告"）引擎支援，但 imaplib 命令以 ASCII 編碼，需 CHARSET——
    # 見引擎層直驅測試 test_p5_search_cjk_subject_via_raw_feed。


def test_p5_search_flagged():
    server = ImapServer({"INBOX": [message(10), message(20, flags={FLAGGED})]})
    m = _imap_over(server)
    m.select("INBOX")
    assert m.uid("search", None, "FLAGGED")[1][0].split() == [b"20"]


def test_p5_search_cjk_subject_via_raw_feed():
    # 引擎支援 CJK 子字串搜尋（imaplib 命令層以 ASCII 編碼故無法直送，這裡以原始行直驅引擎驗證）
    import base64

    server = fresh_server()
    server.feed(b"a1 AUTHENTICATE XOAUTH2")
    server.feed(base64.b64encode(b"x"))
    server.feed(b"a2 SELECT INBOX")
    resp = server.feed('a3 UID SEARCH SUBJECT "報告"'.encode("utf-8"))
    assert b"SEARCH %d" % INBOX_CJK_UID in resp                          # 只命中 102（週報 Q1 報告）


# ── P6：APPEND 同步 literal ──────────────────────────────────────────────────

def test_p6_append_adds_message_and_is_retrievable():
    server = ImapServer({"INBOX": [message(10, "Existing")]})
    m = _imap_over(server)
    raw = b"Subject: Appended note\r\nFrom: a@x.com\r\nTo: me@x.com\r\n\r\nHello body.\r\n"
    typ, dat = m.append("INBOX", None, None, raw)
    assert typ == "OK" and b"APPENDUID" in dat[0]
    assert len(server.mailboxes["INBOX"]) == 2                            # 引擎狀態真的多一封

    m.select("INBOX")
    assert len(m.uid("search", None, "ALL")[1][0].split()) == 2
    new_uid = max(x.uid for x in server.mailboxes["INBOX"])
    typ, fdat = m.uid("fetch", str(new_uid), "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    assert b"Subject: Appended note" in fdat[0][1]                        # 附加的內容可取回


def test_p6_append_to_missing_mailbox_is_trycreate():
    server = ImapServer({"INBOX": []})
    m = _imap_over(server)
    typ, dat = m.append("NoSuchBox", None, None, b"Subject: x\r\n\r\n")
    assert typ == "NO" and b"TRYCREATE" in dat[0]


# ── P7：範圍 FETCH BODY[]<offset.length> ─────────────────────────────────────

def test_p7_partial_fetch_returns_slice_with_offset_label():
    server = ImapServer(mime_mailboxes())
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_PLAIN_UID), "(BODY.PEEK[]<0.10>)")
    assert typ == "OK"
    meta, literal = data[0]
    assert b"BODY[]<0>" in meta and len(literal) == 10                    # 標 <offset>、取前 10 bytes
    # 與整封前 10 bytes 相符
    typ, full = m.uid("fetch", str(MIME_PLAIN_UID), "(BODY.PEEK[])")
    assert full[0][1][:10] == literal


# ── P8：超大 literal（~1MB）round-trip ───────────────────────────────────────

def test_p8_large_literal_roundtrips():
    big = "X" * 1_000_000
    server = ImapServer({"INBOX": [mime_message(10, "Big", text=big)]})
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", "10", "(UID RFC822.SIZE BODY.PEEK[TEXT])")
    assert typ == "OK"
    body = data[0][1]
    assert body.count(b"X") == 1_000_000                                 # 百萬位元組精確讀回


# ── P9：greeting 變體 ────────────────────────────────────────────────────────

def test_p9_preauth_greeting_starts_authenticated():
    server = ImapServer({"INBOX": []}, greeting_mode="preauth")
    m = SimIMAP4_SSL(server)                                              # 不認證
    assert m.state == "AUTH"                                             # imaplib 由 * PREAUTH 設為 AUTH


def test_p9_greeting_without_caps_triggers_capability_command():
    server = ImapServer({"INBOX": []}, greeting_mode="no_caps")
    SimIMAP4_SSL(server)
    assert server.command_count("CAPABILITY") >= 1                       # imaplib 另送 CAPABILITY 探測


# ── P10：畸形 tagged 行 → 受控 abort（協定健壯）─────────────────────────────

def test_p10_garbage_tagged_line_causes_controlled_abort():
    server = ImapServer({"INBOX": [message(10, "A")]})
    server.arm_unsolicited("FETCH", line=b"zz99 OK spurious tagged response")
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    with pytest.raises(imaplib.IMAP4.abort):                             # 非預期 tag → 大聲中止，不靜默誤判
        m.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")


# ── P11：STATUS / NAMESPACE / LSUB / CONDSTORE ───────────────────────────────

def test_p11_status_returns_counts():
    m = _imap_over(fresh_server())
    typ, dat = m.status("INBOX", "(MESSAGES UIDNEXT UIDVALIDITY UNSEEN)")
    assert typ == "OK" and b'"INBOX"' in dat[0]
    assert b"MESSAGES 8" in dat[0] and b"UIDNEXT 109" in dat[0]


def test_p11_namespace_and_lsub():
    m = _imap_over(fresh_server())
    typ, dat = m.namespace()
    assert typ == "OK" and b'"/"' in dat[0]
    typ, lines = m.lsub()
    assert typ == "OK" and any(b"INBOX" in ln for ln in lines)


def test_p11_condstore_highestmodseq_and_fetch_modseq():
    server = fresh_server(supports_condstore=True)
    m = _imap_over(server)
    typ, _dat = m.select("INBOX")
    assert m.untagged_responses.get("HIGHESTMODSEQ") is not None         # SELECT 報 HIGHESTMODSEQ
    typ, fdat = m.uid("fetch", str(INBOX_NEWSLETTER_UID), "(UID MODSEQ)")
    meta = fdat[0] if isinstance(fdat[0], bytes) else fdat[0][0]
    assert b"MODSEQ (" in meta                                           # FETCH 回 MODSEQ
