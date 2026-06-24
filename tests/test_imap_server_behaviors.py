"""引擎伺服器端行為的自我保真測試 —— 以「真 imaplib 直接驅動引擎」驗證產品 happy-path
碰不到的協定/邊角行為（比 FakeIMAPConn 更忠實：回應全程走真 imaplib 解析）。

技術：`SimIMAP4_SSL(server)` 直接建一個連上引擎的**真 imaplib** 客戶端（greeting+CAPABILITY
由真 imaplib 跑完），再手動認證進 AUTH，然後下產品不會送的原始指令（無 UID 的 FETCH、
SELECT 不存在夾、整夾 EXPUNGE、無 UIDPLUS 的 UID EXPUNGE…）。這正是 FakeIMAPConn 時代
`test_imap_sim.py` / `test_imap_fidelity.py` 驗的「模擬器定義性行為」，現改由引擎承接。
"""
from __future__ import annotations

from imap_server import ImapServer
from imap_sim import DELETED, message
from imap_transport import SimIMAP4_SSL

from mailkeeper.imap_client import _parse_folder_name


def _imap_over(server: ImapServer):
    """連上引擎、已認證的**真 imaplib** 客戶端（AUTH 狀態）。

    用於直接驅動產品不會送的原始 IMAP 指令——回應仍由真 imaplib 解析（保真度最大）。
    """
    m = SimIMAP4_SSL(server)  # 真 imaplib._connect：讀 greeting + 送 CAPABILITY
    m.authenticate("XOAUTH2", lambda _challenge: b"user=u\x01auth=Bearer t\x01\x01")
    return m


# ── FETCH：多封 wire 結構（真 imaplib 解析引擎回應）─────────────────────────

def test_engine_fetch_multiple_messages_parsed_by_real_imaplib():
    # 兩封 → 兩組 (metadata, literal) 交錯 b')'；每封 metadata 帶自己的 UID、literal 帶自己的標頭
    server = ImapServer({"INBOX": [message(10, "A"), message(11, "B")]})
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", "10,11", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    assert typ == "OK"
    tuples = [d for d in data if isinstance(d, tuple)]
    assert len(tuples) == 2 and data.count(b")") == 2
    assert b"UID 10" in tuples[0][0] and b"Subject: A" in tuples[0][1]
    assert b"UID 11" in tuples[1][0] and b"Subject: B" in tuples[1][1]


def test_engine_fetch_omits_uid_when_not_requested():
    # 忠實鐵則（0.5.0 致命 bug 的觸發條件）：沒索取 UID → 伺服器就不回 UID（與真實 Outlook 一致）
    server = ImapServer({"INBOX": [message(10, "A")]})
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", "10", "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")  # 刻意不索取 UID
    assert typ == "OK"
    assert b"UID" not in data[0][0]  # metadata 不含 UID


def test_engine_fetch_includes_uid_before_body_when_requested():
    server = ImapServer({"INBOX": [message(10, "A")]})
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    meta = data[0][0]
    assert b"UID 10" in meta and meta.index(b"UID") < meta.index(b"BODY")  # UID 在 BODY 之前


# ── SEARCH / LIST 格式（真 imaplib 解析引擎回應）────────────────────────────

def test_engine_search_returns_space_joined_uids():
    server = ImapServer({"INBOX": [message(10), message(11), message(12)]})
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    assert m.uid("search", None, "ALL") == ("OK", [b"10 11 12"])


def test_engine_list_mutf7_parsed_by_real_imaplib_and_product_decoder():
    # LIST 回應經真 imaplib 解析；CJK 夾以 modified-UTF-7 編碼，且能被產品 decoder 還原
    server = ImapServer({"INBOX": [], "台北": [], "Work/Projects": []})
    m = _imap_over(server)
    typ, lines = m.list()
    assert typ == "OK"
    assert any(b'(\\HasNoChildren) "/" "INBOX"' == ln for ln in lines)  # imaplib 風格行
    cjk = next(ln for ln in lines if b"&U,BTFw-" in ln)               # 台北 的 mUTF-7
    assert _parse_folder_name(cjk) == "台北"


def test_engine_select_missing_mailbox_returns_no():
    server = ImapServer({"INBOX": []})
    m = _imap_over(server)
    assert m.select("Nope")[0] == "NO"  # 不存在夾 → NO（[NONEXISTENT]）


# ── EXPUNGE 波及範圍（move() 資料遺失 bug 的核心語意）────────────────────────

def test_engine_full_expunge_removes_all_deleted():
    # 整夾 EXPUNGE 清掉選取夾**所有** \Deleted（這正是 0.5.1 改用 UID EXPUNGE 要避開的危險語意）
    server = ImapServer(
        {"INBOX": [message(10, "A", flags={DELETED}), message(20, "B", flags={DELETED}), message(30, "C")]}
    )
    m = _imap_over(server)
    m.select("INBOX")
    m.expunge()
    assert {x.uid for x in server.mailboxes["INBOX"]} == {30}  # 兩封已標刪皆清掉、未標刪保留


def test_engine_uid_expunge_only_targets_given_deleted_uid():
    # UID EXPUNGE 只清「指定且已標 \Deleted」者（RFC 4315 UIDPLUS）
    server = ImapServer(
        {"INBOX": [message(10, "A", flags={DELETED}), message(20, "B", flags={DELETED})]}
    )
    m = _imap_over(server)
    m.select("INBOX")
    assert m.uid("expunge", "10")[0] == "OK"
    assert {x.uid for x in server.mailboxes["INBOX"]} == {20}  # 只清 10，20 保留


def test_engine_uid_expunge_returns_no_without_uidplus():
    # 伺服器無 UIDPLUS → UID EXPUNGE 回 NO（驅動產品 fallback 到整夾 EXPUNGE）
    server = ImapServer({"INBOX": [message(10, "A", flags={DELETED})]}, supports_uidplus=False)
    m = _imap_over(server)
    m.select("INBOX")
    assert m.uid("expunge", "10")[0] == "NO"
    assert any(x.uid == 10 for x in server.mailboxes["INBOX"])  # 未清除


# ── COPY / MOVE 語意 ───────────────────────────────────────────────────────

def test_engine_copy_to_missing_folder_returns_no_trycreate():
    server = ImapServer({"INBOX": [message(10, "A")]})
    m = _imap_over(server)
    m.select("INBOX")
    typ, dat = m.uid("copy", "10", "NoSuchFolder")
    assert typ == "NO" and b"TRYCREATE" in dat[0]


def test_engine_copy_assigns_new_uid_and_keeps_source():
    server = ImapServer({"INBOX": [message(10, "A")], "Archive": []})
    m = _imap_over(server)
    m.select("INBOX")
    assert m.uid("copy", "10", "Archive")[0] == "OK"
    assert any(x.uid == 10 for x in server.mailboxes["INBOX"])      # 來源仍在
    assert len(server.mailboxes["Archive"]) == 1
    assert server.mailboxes["Archive"][0].uid != 10                # 目標配發新 UID


def test_engine_move_unsupported_returns_no():
    server = ImapServer({"INBOX": [message(10, "A")], "Archive": []}, supports_move=False)
    m = _imap_over(server)
    m.select("INBOX")
    assert m.uid("move", "10", "Archive")[0] == "NO"
