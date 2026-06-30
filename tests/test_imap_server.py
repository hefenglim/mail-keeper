"""線級 IMAP 引擎（方案 B）P1 讀取路徑驗收 —— 真 imaplib 跑在引擎上。

驗證策略（三角驗證，可信度最大化）：
  A. **真產品跑真 imaplib over 引擎**：``OutlookIMAPClient`` 連線/列夾/列標頭全程零改動，結果正確
     → 證明引擎的 wire 序列化能被真 imaplib 解析、且語意正確（比任何假物替身更強）。
  B. **引擎 ≡ 真 imaplib**：把引擎吐的 wire 餵進 ``imaplib_probe``（真 imaplib 解析），斷言解析出的
     結構與 literal 位元組逐一正確（不依賴任何假物——真 imaplib 才是規格基準）。
  C. **雙層驗證**：結構化命令 log（送出指令/只讀/影響 UID 正確、UID 不變量）+ 狀態快照（讀取不變更）。
"""
from __future__ import annotations

import base64
import email
import re

import pytest

import imaplib_probe as probe
from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_EMOJI_UID,
    INBOX_EMPTY_SUBJECT_UID,
    INBOX_LONG_SUBJECT_UID,
    INBOX_QUOTED_FROM_UID,
    master_mailboxes,
)
from imap_server import ImapServer
from imap_sim import message
from imap_transport import SimIMAP4_SSL, connected_client, install_server

from mailkeeper.imap_client import BackendError, OutlookIMAPClient, _decode

_ITEMS = "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])"


def _server(**kw) -> ImapServer:
    return ImapServer(master_mailboxes(), **kw)


# ── A. 真產品跑真 imaplib over 引擎 ─────────────────────────────────────────

def test_real_client_lists_headers_over_engine(monkeypatch):
    headers = connected_client(monkeypatch, _server()).list_headers("INBOX")
    uids = [h.uid for h in headers]
    assert uids == [str(u) for u in range(101, 109)]  # 母版 INBOX 101..108 依序對位
    assert all(uids)  # 全部非空（0.5.1 致命 bug 守衛——但這次是真 imaplib 解析的結果）


def test_real_client_decodes_cjk_emoji_quoted_and_empty(monkeypatch):
    headers = {h.uid: h for h in connected_client(monkeypatch, _server()).list_headers("INBOX")}
    assert headers[str(INBOX_CJK_UID)].subject == "週報 Q1 報告"          # encoded-word 解碼
    assert headers[str(INBOX_CJK_UID)].sender == "王經理 <boss@x.com>"     # CJK 顯示名還原
    assert "🎉" in headers[str(INBOX_EMOJI_UID)].subject                   # emoji
    assert headers[str(INBOX_QUOTED_FROM_UID)].sender.startswith('"Serena Yeh"')  # 帶引號顯示名
    assert headers[str(INBOX_EMPTY_SUBJECT_UID)].subject == ""             # 空主旨
    assert headers[str(INBOX_LONG_SUBJECT_UID)].subject == "L" * 200       # 超長主旨


def test_real_client_lists_folders_over_engine(monkeypatch):
    folders = connected_client(monkeypatch, _server()).list_folders()
    assert set(folders) == {"INBOX", "Sent", "Archive", "Work/Projects", "台北", "R&D", "VIP客戶"}  # CJK/巢狀/字面&/混合經 mUTF-7


def test_list_headers_raises_if_server_drops_uid(monkeypatch):
    # 防線（0.5.1 致命 bug）：伺服器壞掉（索取了卻不回 UID）→ 大聲報錯，不靜默吐空 uid
    with pytest.raises(BackendError):
        connected_client(monkeypatch, _server(drop_uid=True)).list_headers("INBOX")


def test_list_headers_raises_on_batch_fetch_failure(monkeypatch):
    # 防線：批次 FETCH 回 NO → 大聲報錯，不靜默回傳不完整標頭
    with pytest.raises(BackendError):
        connected_client(monkeypatch, _server(fail_fetch=True)).list_headers("INBOX")


def test_context_manager_connects_and_logs_out_over_engine(monkeypatch):
    server = _server()
    install_server(monkeypatch, server)
    with OutlookIMAPClient("me@x.com", "tok"):
        pass
    assert server.command_count("AUTHENTICATE") == 1 and server.command_count("LOGOUT") == 1


def test_connect_builds_exact_xoauth2_auth_string(monkeypatch):
    server = _server()
    install_server(monkeypatch, server)
    OutlookIMAPClient("me@x.com", "tok123").connect()
    # 真 imaplib 的 AUTHENTICATE 續傳：base64(SASL) 經引擎解回原字串（注意 \x01 控制字元）
    assert server.auth_string == b"user=me@x.com\x01auth=Bearer tok123\x01\x01"


def test_connect_passes_configured_timeout(monkeypatch):
    server = _server()
    cap = install_server(monkeypatch, server)
    OutlookIMAPClient("me@x.com", "tok", timeout=42).connect()
    assert cap["timeout"] == 42 and cap["constructed"] == 1


# ── B. 引擎 ≡ 真 imaplib（引擎吐的 wire 經真 imaplib 解析，斷言結構/位元組正確，無假物依賴）──

def test_engine_fetch_wire_matches_real_imaplib():
    server = ImapServer({"INBOX": [message(10, "Hello", "a@x.com", "me@x.com", "Mon, 1 Jan 2026")]})
    server.feed(b"a1 CAPABILITY")
    server.feed(b"a2 AUTHENTICATE XOAUTH2")
    server.feed(base64.b64encode(b"user=me\x01auth=Bearer t\x01\x01"))
    server.feed(b"a3 EXAMINE INBOX")
    resp = server.feed(b"a4 UID FETCH 10 " + _ITEMS.encode())
    wire = resp.split(b"a4 OK")[0]  # 取 untagged FETCH 部分（含 literal），其餘交給 probe 補 tagged

    typ, data = probe.real_uid_fetch(wire, "10", _ITEMS)
    assert typ == "OK"
    meta, literal = data[0]
    assert b"UID 10" in meta and b"BODY[HEADER.FIELDS" in meta  # metadata 帶 UID + BODY
    assert data[1] == b")"                                       # literal 後接 )
    # literal 為 RFC 標頭區塊、位元組逐一相符（真 imaplib 依宣告的 {N} 精確讀取）
    assert literal == b"Subject: Hello\r\nFrom: a@x.com\r\nTo: me@x.com\r\nDate: Mon, 1 Jan 2026\r\n\r\n"


def test_engine_fetch_wire_tricky_subjects_byte_lengths_correct():
    # 危險母版郵件（CJK/emoji/空/超長）的 FETCH wire 經真 imaplib 解析：encoded-word literal 的
    # {N} 位元組數正確（真 imaplib 會精確讀 {N} 位元組，錯了即解析失敗），且能還原回原主旨。
    tricky_uids = (INBOX_CJK_UID, INBOX_EMOJI_UID, INBOX_EMPTY_SUBJECT_UID, INBOX_LONG_SUBJECT_UID)
    inbox = [m for m in master_mailboxes()["INBOX"] if m.uid in tricky_uids]
    by_uid = {m.uid: m for m in inbox}

    server = ImapServer({"INBOX": list(inbox)})
    server.feed(b"a2 AUTHENTICATE XOAUTH2")
    server.feed(base64.b64encode(b"x"))
    server.feed(b"a3 EXAMINE INBOX")
    uidset = ",".join(str(u) for u in tricky_uids)
    resp = server.feed(f"a4 UID FETCH {uidset} ".encode() + _ITEMS.encode())
    wire = resp.split(b"a4 OK")[0]

    typ, data = probe.real_uid_fetch(wire, uidset, _ITEMS)
    assert typ == "OK"
    tuples = [d for d in data if isinstance(d, tuple)]
    assert len(tuples) == len(tricky_uids)  # 四封皆完整解析（literal {N} 全部正確）
    for meta, literal in tuples:
        uid = int(re.search(rb"UID (\d+)", meta).group(1))  # type: ignore[union-attr]
        subject = _decode(email.message_from_bytes(literal).get("Subject"))
        assert subject == by_uid[uid].fields["SUBJECT"]  # 真 imaplib 讀出的 literal 還原回原主旨


def test_engine_search_wire_matches_real_imaplib():
    server = ImapServer({"INBOX": [message(10), message(11), message(12)]})
    server.feed(b"a2 AUTHENTICATE XOAUTH2")
    server.feed(base64.b64encode(b"x"))
    server.feed(b"a3 EXAMINE INBOX")
    resp = server.feed(b"a4 UID SEARCH ALL")
    wire = resp.split(b"a4 OK")[0]
    assert probe.real_uid_search(wire) == ("OK", [b"10 11 12"])


# ── C. 雙層驗證：結構化命令 log + 狀態快照 ──────────────────────────────────

def test_structured_log_records_readonly_select_search_fetch(monkeypatch):
    server = _server()
    connected_client(monkeypatch, server).list_headers("INBOX")
    cmds = [op.command for op in server.log]
    assert cmds.count("AUTHENTICATE") == 1
    assert "EXAMINE" in cmds and "UID SEARCH" in cmds and "UID FETCH" in cmds
    examine = server.commands("EXAMINE")[0]
    assert examine.mailbox == "INBOX" and examine.response_code == "READ-ONLY"  # 匯出用只讀
    fetch = server.commands("UID FETCH")[0]
    assert set(fetch.affected_uids) == set(range(101, 109))


def test_uid_request_invariant_holds(monkeypatch):
    # 釘死 0.5.x 回歸類：每個 FETCH 都索取 UID（請求端不變量，非只看回應）
    server = _server()
    connected_client(monkeypatch, server).list_headers("INBOX")
    server.assert_all_fetches_request_uid()
    assert server.fetch_count("INBOX") == 1  # 整夾標頭只抓一次（≤50 封一批）


def test_readonly_export_does_not_mutate_state(monkeypatch):
    server = _server()
    before = server.snapshot()
    connected_client(monkeypatch, server).list_headers("INBOX")
    assert server.snapshot() == before  # 匯出（EXAMINE + 唯讀 FETCH）零資料變動


def test_transport_is_real_imap4ssl_subclass():
    # 守住「上層跑的是真 imaplib」這條根本不變量
    assert issubclass(SimIMAP4_SSL, __import__("imaplib").IMAP4_SSL)
