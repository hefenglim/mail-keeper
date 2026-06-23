"""OutlookIMAPClient 契約測試 —— 一律對母版資料集 (imap_dataset.fresh_sim) 的模擬器執行。

查核：
  1. 送出的 IMAP 指令正確、安全、符合規格（透過動作日誌 sim.log）。
  2. 回應被正確解析（這批測試本可在 0.5.0 就抓到 UID 全空的致命 bug）。
  3. 破壞性動作（move/expunge）不造成資料遺失。
所有情境都從 fresh_sim() 複製一份獨立母版出發。
"""
from __future__ import annotations

import pytest

from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_NEWSLETTER_UID,
    INBOX_USER_DELETED_UID,
    fresh_sim,
)
from imap_sim import FLAGGED, SEEN, client_on, install

from mailkeeper.imap_client import BackendError, OutlookIMAPClient


# --- list_headers：契約 + 動作日誌（本可在 0.5.0 抓到 UID 全空 bug）---

def test_list_headers_uids_populated_against_master():
    headers = client_on(fresh_sim()).list_headers("INBOX")
    uids = [h.uid for h in headers]
    assert uids == [str(u) for u in range(101, 109)]  # 母版 INBOX uid 101..108，依序對位
    assert all(uids)  # 全部非空（0.5.1 致命 bug 守衛）


def test_list_headers_fetch_requests_uid_and_is_readonly():
    # 把契約釘在「請求端」：每個 FETCH 必索取 UID，且匯出/檢驗用只讀 SELECT（不改信箱）
    sim = fresh_sim()
    client_on(sim).list_headers("INBOX")
    fetches = sim.uid_commands("fetch")
    assert fetches and all("UID" in f.args[2] for f in fetches)
    assert sim.commands("select")[0].kwargs.get("readonly") is True


def test_list_headers_every_uid_nonempty_invariant():
    headers = client_on(fresh_sim()).list_headers("INBOX")
    assert len(headers) == 8 and all(h.uid for h in headers)


def test_list_headers_raises_if_server_drops_uid():
    # 防線：伺服器壞掉（索取了卻不回 UID）→ 大聲報錯，不靜默吐空 uid
    with pytest.raises(BackendError):
        client_on(fresh_sim(drop_uid=True)).list_headers("INBOX")


def test_list_headers_raises_on_batch_failure():
    # 批次 FETCH 回 NO → 大聲報錯，不靜默回傳不完整標頭
    with pytest.raises(BackendError):
        client_on(fresh_sim(fail_fetch=True)).list_headers("INBOX")


def test_list_headers_decodes_cjk_fields():
    headers = client_on(fresh_sim()).list_headers("INBOX")
    cjk = next(h for h in headers if h.uid == str(INBOX_CJK_UID))
    assert cjk.subject == "週報 Q1 報告"          # encoded-word 解碼
    assert cjk.sender == "王經理 <boss@x.com>"     # 含 CJK 顯示名亦正確還原
    assert cjk.recipients == "me@outlook.my"


# --- list_folders 契約（含 CJK / 巢狀夾，經 modified-UTF-7）---

def test_list_folders_against_master():
    folders = client_on(fresh_sim()).list_folders()
    assert set(folders) == {"INBOX", "Sent", "Archive", "Work/Projects", "台北"}


# --- move：happy path（UID MOVE）---

def test_move_uses_uid_move_and_moves_only_target():
    sim = fresh_sim()
    client_on(sim).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    inbox = {m.uid for m in sim.mailboxes["INBOX"]}
    assert INBOX_NEWSLETTER_UID not in inbox and len(inbox) == 7  # 只少了目標那封
    assert len(sim.mailboxes["Archive"]) == 1
    assert sim.uid_commands("move") and not sim.commands("expunge")  # 用 MOVE，不走 expunge


# --- move：fallback 資料安全 ---

def test_move_fallback_does_not_delete_when_copy_fails():
    # 不支援 MOVE → fallback；copy 因目標夾不存在而失敗 → 絕不標刪+expunge，來源郵件須留存
    sim = fresh_sim(supports_move=False)
    with pytest.raises(BackendError):
        client_on(sim).move(str(INBOX_NEWSLETTER_UID), "NoSuchFolder", "INBOX")
    assert any(m.uid == INBOX_NEWSLETTER_UID for m in sim.mailboxes["INBOX"])
    assert not sim.uid_commands("store")
    assert not sim.commands("expunge") and not sim.uid_commands("expunge")


def test_move_fallback_spares_foreign_deleted_message():
    # fallback 刪除只能波及目標；母版內他人已標 \Deleted 的郵件（106）不可被連坐清掉
    sim = fresh_sim(supports_move=False)
    client_on(sim).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    inbox = {m.uid for m in sim.mailboxes["INBOX"]}
    assert INBOX_USER_DELETED_UID in inbox  # 關鍵安全點
    assert INBOX_NEWSLETTER_UID not in inbox
    assert len(sim.mailboxes["Archive"]) == 1
    assert sim.uid_commands("expunge") and not sim.commands("expunge")  # UID EXPUNGE，非整夾


def test_move_fallback_completes_when_copy_succeeds():
    sim = fresh_sim(supports_move=False)
    client_on(sim).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert not any(m.uid == INBOX_NEWSLETTER_UID for m in sim.mailboxes["INBOX"])
    assert len(sim.mailboxes["Archive"]) == 1


# --- mark_read / flag 契約 ---

def test_mark_read_sets_seen_flag():
    sim = fresh_sim()
    client_on(sim).mark_read(str(INBOX_NEWSLETTER_UID), "INBOX")
    msg = next(m for m in sim.mailboxes["INBOX"] if m.uid == INBOX_NEWSLETTER_UID)
    assert SEEN in msg.flags
    store = sim.uid_commands("store")[0]
    assert store.args[2] == "+FLAGS" and store.args[3] == "(\\Seen)"


def test_flag_sets_flagged():
    sim = fresh_sim()
    client_on(sim).flag(str(INBOX_NEWSLETTER_UID), "INBOX")
    msg = next(m for m in sim.mailboxes["INBOX"] if m.uid == INBOX_NEWSLETTER_UID)
    assert FLAGGED in msg.flags


# --- connect()：XOAUTH2 認證字串格式（FakeIMAPConn + install）---

def test_connect_builds_exact_xoauth2_auth_string(monkeypatch):
    sim = fresh_sim()
    install(monkeypatch, sim)
    OutlookIMAPClient("me@x.com", "tok123").connect()
    assert sim.commands("authenticate")[0].args[0] == "XOAUTH2"
    # 注意是 \x01 控制字元、Bearer 前綴、雙 \x01 結尾
    assert sim.auth_string == b"user=me@x.com\x01auth=Bearer tok123\x01\x01"


def test_connect_passes_configured_timeout(monkeypatch):
    sim = fresh_sim()
    cap = install(monkeypatch, sim)
    OutlookIMAPClient("me@x.com", "tok", timeout=42).connect()
    assert cap["timeout"] == 42 and cap["constructed"] == 1


def test_context_manager_connects_and_logs_out(monkeypatch):
    sim = fresh_sim()
    install(monkeypatch, sim)
    with OutlookIMAPClient("me@x.com", "tok"):
        pass
    assert [c.name for c in sim.log] == ["authenticate", "logout"]  # 進入認證、離開登出
