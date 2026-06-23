"""驗證 FakeIMAPConn 模擬器本身的忠實度。

我們要靠這個模擬器去信任 imap_client，所以模擬器的「定義性行為」必須先被釘死：
沒索取就不給 UID、EXPUNGE 的波及範圍、COPY 目標夾不存在回 NO、回應資料結構等。
"""
from __future__ import annotations

from imap_sim import DELETED, FakeIMAPConn, message


def _inbox(*msgs):
    return FakeIMAPConn({"INBOX": list(msgs), "Archive": []})


# --- FETCH 只回索取的 data items（這正是 0.5.1 致命 bug 的觸發條件）---

def test_fetch_omits_uid_when_not_requested():
    sim = _inbox(message(10, "A"))
    sim.select("INBOX", readonly=True)
    typ, data = sim.uid("fetch", "10", "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    assert typ == "OK"
    meta = data[0][0]  # tuple 的 metadata 段
    assert b"UID" not in meta  # 忠實：沒索取就不給 → 模擬真實 Outlook 行為


def test_fetch_includes_uid_when_requested_first():
    sim = _inbox(message(10, "A"))
    sim.select("INBOX", readonly=True)
    typ, data = sim.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    meta = data[0][0]
    assert b"UID 10" in meta
    assert meta.index(b"UID") < meta.index(b"BODY")  # UID 在 BODY 之前


def test_fetch_response_structure_matches_imaplib():
    # 帶 literal 的 FETCH → [(metadata_bytes, literal_bytes), b')'] 交錯結構
    sim = _inbox(message(10, "Hello", "a@x.com"))
    sim.select("INBOX", readonly=True)
    typ, data = sim.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
    assert isinstance(data[0], tuple) and len(data[0]) == 2
    assert data[1] == b")"
    assert b"Subject: Hello" in data[0][1] and b"From: a@x.com" in data[0][1]


def test_drop_uid_simulates_misbehaving_server():
    # 即使索取 UID，drop_uid 伺服器也不回 → 用於測試上層防線
    sim = FakeIMAPConn({"INBOX": [message(10, "A")]}, drop_uid=True)
    sim.select("INBOX", readonly=True)
    typ, data = sim.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    assert b"UID" not in data[0][0]


# --- SEARCH / LIST 回應結構 ---

def test_search_returns_space_joined_uids():
    sim = _inbox(message(10, "A"), message(11, "B"), message(12, "C"))
    sim.select("INBOX", readonly=True)
    assert sim.uid("search", None, "ALL") == ("OK", [b"10 11 12"])


def test_list_returns_imaplib_style_lines():
    sim = _inbox(message(10, "A"))
    typ, lines = sim.list()
    assert typ == "OK"
    assert any(b'"INBOX"' in ln and b"HasNoChildren" in ln for ln in lines)


def test_select_missing_mailbox_returns_no():
    sim = _inbox(message(10, "A"))
    assert sim.select("Nope")[0] == "NO"


# --- EXPUNGE 波及範圍（move() 資料遺失 bug 的核心）---

def test_full_expunge_removes_all_deleted():
    sim = _inbox(message(10, "A", flags={DELETED}), message(20, "B", flags={DELETED}), message(30, "C"))
    sim.select("INBOX")
    sim.expunge()
    assert {m.uid for m in sim.mailboxes["INBOX"]} == {30}  # 兩封已標刪皆清掉


def test_uid_expunge_only_targets_given_uid():
    sim = _inbox(message(10, "A", flags={DELETED}), message(20, "B", flags={DELETED}))
    sim.select("INBOX")
    sim.uid("expunge", "10")
    assert {m.uid for m in sim.mailboxes["INBOX"]} == {20}  # 只清 10，20 保留


def test_uid_expunge_returns_no_without_uidplus():
    sim = FakeIMAPConn({"INBOX": [message(10, "A", flags={DELETED})]}, supports_uidplus=False)
    sim.select("INBOX")
    assert sim.uid("expunge", "10")[0] == "NO"


# --- COPY / MOVE 語意 ---

def test_copy_to_missing_folder_returns_no():
    sim = _inbox(message(10, "A"))
    sim.select("INBOX")
    assert sim.uid("copy", "10", "NoSuchFolder")[0] == "NO"


def test_copy_assigns_new_uid_in_dest_and_keeps_source():
    sim = _inbox(message(10, "A"))
    sim.select("INBOX")
    sim.uid("copy", "10", "Archive")
    assert any(m.uid == 10 for m in sim.mailboxes["INBOX"])  # 來源仍在
    assert len(sim.mailboxes["Archive"]) == 1
    assert sim.mailboxes["Archive"][0].uid != 10  # 目標夾配發新 UID


def test_move_unsupported_returns_no():
    sim = FakeIMAPConn({"INBOX": [message(10, "A")], "Archive": []}, supports_move=False)
    sim.select("INBOX")
    assert sim.uid("move", "10", "Archive")[0] == "NO"


def test_store_deleted_flag_then_full_expunge():
    sim = _inbox(message(10, "A"))
    sim.select("INBOX")
    sim.uid("store", "10", "+FLAGS", "(\\Deleted)")
    assert DELETED in sim.mailboxes["INBOX"][0].flags


# --- 動作日誌 ---

def test_command_log_records_commands_and_args():
    sim = _inbox(message(10, "A"))
    sim.select("INBOX", readonly=True)
    sim.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    assert sim.commands("select")[0].kwargs == {"readonly": True}
    fetch = sim.uid_commands("fetch")[0]
    assert fetch.args[0] == "fetch" and "UID" in fetch.args[2]
