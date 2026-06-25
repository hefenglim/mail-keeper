"""IMAP 模擬器引擎 —— MIME 內文 / 附件建模（E11）驗收。

涵蓋規格書 §4A 的 DATA-4：引擎能服務 ``BODY[]`` / ``BODY[TEXT]`` / ``RFC822`` / ``RFC822.SIZE`` /
``BODYSTRUCTURE``，且回應 wire 經**真 imaplib** 解析後能還原回原郵件（純文字 / multipart-alternative
含 CJK / 帶附件）。產品現只抓 HEADER.FIELDS；此地基供未來抓內文/附件的開發直接以引擎實測。

技術：``_imap_over`` = 真 imaplib over 引擎（保真度最大）；另以 ``imaplib_probe`` 對拍引擎吐的 BODY[] wire。
"""
from __future__ import annotations

import email
import email.policy

import imaplib_probe as probe
from imap_dataset import (
    MIME_ALT_UID,
    MIME_ATTACH_UID,
    MIME_PLAIN_UID,
    mime_mailboxes,
)
from imap_server import ImapServer
from imap_sim import SEEN, mime_message
from imap_transport import SimIMAP4_SSL


def _imap_over(server: ImapServer) -> SimIMAP4_SSL:
    m = SimIMAP4_SSL(server)
    m.authenticate("XOAUTH2", lambda _c: b"user=u\x01auth=Bearer t\x01\x01")
    return m


def _parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


def _server() -> ImapServer:
    return ImapServer(mime_mailboxes())


# ── BODY[] 整封 / BODY[TEXT] 內文（真 imaplib round-trip）────────────────────

def test_engine_body_full_roundtrips_via_real_imaplib():
    m = _imap_over(_server())
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_PLAIN_UID), "(UID BODY.PEEK[])")
    assert typ == "OK"
    msg = _parse(data[0][1])
    assert msg["Subject"] == "Plain note"
    body = msg.get_content()
    assert "Hello world." in body and "Second line." in body         # 內文逐字還原（CRLF 行尾）


def test_engine_body_text_section_returns_body_only():
    m = _imap_over(_server())
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_PLAIN_UID), "(UID BODY.PEEK[TEXT])")
    assert typ == "OK"
    text = data[0][1]
    assert b"Hello world." in text and b"Subject:" not in text       # 只回內文、不含表頭


# ── RFC822 / RFC822.SIZE ────────────────────────────────────────────────────

def test_engine_rfc822_full_equals_raw_and_size_matches():
    server = _server()
    raw_len = len(server.mailboxes["INBOX"][0].raw)                  # type: ignore[arg-type]
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_PLAIN_UID), "(UID RFC822.SIZE)")
    assert typ == "OK" and b"RFC822.SIZE %d" % raw_len in data[0]    # 大小 = 整封 bytes 數

    typ, data = m.uid("fetch", str(MIME_PLAIN_UID), "(RFC822)")
    assert _parse(data[0][1])["Subject"] == "Plain note"            # 整封 RFC822 literal 可解析


# ── BODYSTRUCTURE（真 imaplib 原樣擷取、括號平衡）────────────────────────────

def test_engine_bodystructure_reflects_multipart_with_attachment():
    m = _imap_over(_server())
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_ATTACH_UID), "(UID BODYSTRUCTURE)")
    assert typ == "OK"
    meta = data[0] if isinstance(data[0], bytes) else data[0][0]
    assert b"BODYSTRUCTURE" in meta and b'"MIXED"' in meta          # multipart/mixed
    # 保真：body-fld-octets / body-fld-lines 為**編碼後**計數（SR 條件 1）——非解碼後。
    # 內文 "See attached.\r\n" = 15 octets / 1 line（7bit）；csv base64 "YSxiLGMNCjEsMiwzDQo=\r\n" = 22 octets。
    assert b'"TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "7BIT" 15 1' in meta
    assert b'"TEXT" "CSV" ("CHARSET" "US-ASCII") NIL NIL "BASE64" 22 1' in meta


# ── multipart/alternative（text + html，含 CJK 表頭）─────────────────────────

def test_engine_alternative_has_text_and_html_and_decodes_cjk():
    m = _imap_over(_server())
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_ALT_UID), "(UID BODY.PEEK[])")
    msg = _parse(data[0][1])
    assert msg["Subject"] == "週報 內文"                            # CJK 表頭經 encoded-word 還原
    assert msg.is_multipart() and msg.get_content_subtype() == "alternative"
    subtypes = {p.get_content_subtype() for p in msg.iter_parts()}
    assert {"plain", "html"} <= subtypes


def test_engine_attachment_is_present_and_decodable():
    m = _imap_over(_server())
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", str(MIME_ATTACH_UID), "(UID BODY.PEEK[])")
    msg = _parse(data[0][1])
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    att = attachments[0]
    assert att.get_filename() == "report.csv"
    assert att.get_content() == "a,b,c\r\n1,2,3\r\n"                 # 附件位元組逐字還原


# ── 保真：引擎吐的 BODY[] wire 經 imaplib_probe（真 imaplib 解析器）逐位元組正確 ──

def test_engine_body_wire_matches_real_imaplib_via_probe():
    import base64

    server = _server()
    server.feed(b"a2 AUTHENTICATE XOAUTH2")
    server.feed(base64.b64encode(b"x"))
    server.feed(b"a3 EXAMINE INBOX")
    items = "(UID BODY.PEEK[])"
    resp = server.feed(f"a4 UID FETCH {MIME_PLAIN_UID} ".encode() + items.encode())
    wire = resp.split(b"a4 OK")[0]                                  # untagged FETCH（含 literal）

    typ, data = probe.real_uid_fetch(wire, str(MIME_PLAIN_UID), items)
    assert typ == "OK"
    meta, literal = data[0]
    assert b"UID %d" % MIME_PLAIN_UID in meta and b"BODY[]" in meta
    # literal 為整封 RFC822，{N} 位元組數精確（真 imaplib 依宣告精讀），可還原回原郵件
    assert _parse(literal)["Subject"] == "Plain note"


# ── 擬真副作用：非 PEEK 的 BODY[] / RFC822 設 \Seen（產品一律用 PEEK，不受影響）──

def test_non_peek_full_body_sets_seen():
    server = ImapServer({"INBOX": [mime_message(10, "A", text="hi"), mime_message(11, "B", text="yo")]})
    m = _imap_over(server)
    m.select("INBOX")                                              # 可寫
    m.uid("fetch", "10", "(BODY[])")                              # 非 PEEK 整封
    m.uid("fetch", "11", "(BODY.PEEK[])")                         # PEEK
    by_uid = {x.uid: x for x in server.mailboxes["INBOX"]}
    assert SEEN in by_uid[10].flags and SEEN not in by_uid[11].flags
