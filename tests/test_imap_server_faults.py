"""IMAP 模擬器引擎 —— 進階故障注入 / 連線異常 / 狀態機 / 遙測·分析（E1–E10）驗收。

全部跑**真 imaplib over 引擎**（產品零改動或原始行直接驅動引擎）。涵蓋規格書 §2/§3 的：
  * E1 延遲注入 + 讀逾時（timeout 模式）
  * E2 連線期失敗（TCP 逾時 / TLS 握手 / 拒絕 / BYE）
  * E3 截斷 literal 中途斷
  * E4 response code（UNAVAILABLE / OVERQUOTA / 任意 NO·BAD）
  * E5 非預期/畸形行容錯 + 損毀 MIME 容錯
  * E6 限流 / 連線上限
  * E7 非同步 EXPUNGE 推送（in-flight + 真實移除）
  * E8 強制狀態機（非法指令順序）
  * E9 進階遙測（狀態軌跡 / 故障事件 / 時序）
  * E10 序列對齊器
"""
from __future__ import annotations

import base64
import imaplib
import socket
import ssl

import pytest

from imap_dataset import INBOX_NEWSLETTER_UID, fresh_server
from imap_server import ImapServer
from imap_sim import message
from imap_transport import SimIMAP4_SSL, connected_client, install_server

from mailkeeper.imap_client import BackendError, OutlookIMAPClient


def _imap_over(server: ImapServer) -> SimIMAP4_SSL:
    """連上引擎、已認證的真 imaplib 客戶端（直接下原始指令用）。"""
    m = SimIMAP4_SSL(server)
    m.authenticate("XOAUTH2", lambda _c: b"user=u\x01auth=Bearer t\x01\x01")
    return m


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr("mailkeeper.imap_client.time.sleep", lambda s: None)


# ── E1：延遲注入 + 讀逾時 ─────────────────────────────────────────────────────

def test_arm_latency_records_injected_latency_and_timing(monkeypatch):
    server = fresh_server()
    server.arm_latency("FETCH", 2.5)
    connected_client(monkeypatch, server).list_headers("INBOX")

    fetch = server.commands("UID FETCH")[0]
    assert fetch.injected_latency_s == 2.5                       # 該命令帶注入延遲
    assert any(fe["kind"] == "latency" for fe in server.fault_events)
    rep = server.loop_report()
    assert rep["injected_latency_total_s"] == 2.5
    tr = server.timing_report()
    assert tr["total_injected_latency_s"] == 2.5 and tr["slowest_ops"][0][1] == "UID FETCH"


def test_timeout_mode_mid_move_transparently_reconnects(monkeypatch):
    server = fresh_server()
    server.arm_expiry(before_op="move", nth=1, mode="timeout")   # socket.timeout（讀逾時）
    _no_sleep(monkeypatch)
    connected_client(monkeypatch, server, token_provider=lambda: "tok").move(
        str(INBOX_NEWSLETTER_UID), "Archive", "INBOX"
    )
    assert INBOX_NEWSLETTER_UID not in {u for u, _ in server.snapshot()["INBOX"]}  # 透明恢復後搬成功
    assert server.command_count("AUTHENTICATE") >= 2
    assert any(fe["detail"] == "timeout" for fe in server.fault_events)


# ── E2：連線期失敗 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "mode,exc",
    [("timeout", OSError), ("tls", ssl.SSLError), ("refused", OSError), ("bye", imaplib.IMAP4.error)],
)
def test_connect_failure_modes_raise(monkeypatch, mode, exc):
    server = fresh_server()
    server.arm_connect_failure(mode=mode, nth=1)
    install_server(monkeypatch, server)
    with pytest.raises(exc):
        OutlookIMAPClient("me@x.com", "tok").connect()
    assert any(fe["kind"] in ("connect",) for fe in server.fault_events)


def test_connect_failure_during_reconnect_propagates(monkeypatch):
    server = fresh_server()
    server.arm_expiry(before_op="move", nth=1, mode="eof")      # 觸發重連
    server.arm_connect_failure(mode="timeout", nth=2)          # 第 2 次連線（重連）逾時
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    with pytest.raises(OSError):
        client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert INBOX_NEWSLETTER_UID in {u for u, _ in server.snapshot()["INBOX"]}  # 未搬走


# ── E6：限流 / 連線上限 ──────────────────────────────────────────────────────

def test_max_connections_rejects_second_connection(monkeypatch):
    server = fresh_server(max_connections=1)
    connected_client(monkeypatch, server)                       # 第 1 條連線 OK
    assert server.command_count("AUTHENTICATE") == 1
    with pytest.raises(imaplib.IMAP4.error):                    # 第 2 條 → * BYE → imaplib error
        OutlookIMAPClient("me@x.com", "tok").connect()
    assert any(fe["kind"] == "ratelimit" for fe in server.fault_events)


# ── E3：截斷 literal 中途斷 ──────────────────────────────────────────────────

def test_truncated_fetch_makes_real_imaplib_abort():
    # 引擎層：FETCH 回應自尾端截斷 → 真 imaplib 讀不到 tagged → 受控 abort（不 hang、不崩）
    server = ImapServer({"INBOX": [message(10, "A")]})
    server.arm_truncate("FETCH", drop=25)
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    with pytest.raises(imaplib.IMAP4.abort):
        m.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    assert any(fe["kind"] == "truncate" for fe in server.fault_events)


def test_product_recovers_from_one_truncated_fetch(monkeypatch):
    # 產品層：截斷一次 → 透明重連整批重抓 → 標頭完整、UID 全非空（截斷為一次性、重連後已消）
    server = fresh_server()
    server.arm_truncate("FETCH", drop=20)
    _no_sleep(monkeypatch)
    headers = connected_client(monkeypatch, server, token_provider=lambda: "tok").list_headers("INBOX")
    assert len(headers) == 8 and all(h.uid for h in headers)
    assert server.command_count("AUTHENTICATE") >= 2


# ── E4：response code（UNAVAILABLE / OVERQUOTA / 任意 NO·BAD）─────────────────

def test_arm_response_unavailable_code_parsed_by_real_imaplib():
    server = ImapServer({"INBOX": [message(10)]})
    server.arm_response("EXAMINE", code="UNAVAILABLE", text="Server busy, retry later")
    m = _imap_over(server)
    typ, _dat = m.select("INBOX", readonly=True)               # imaplib：readonly → 送 EXAMINE
    assert typ == "NO" and m.untagged_responses.get("UNAVAILABLE") is not None
    assert any(fe["kind"] == "response" for fe in server.fault_events)


def test_arm_response_overquota_on_copy_keeps_source(monkeypatch):
    # 擬真：COPY 因 [OVERQUOTA] 失敗 → 產品大聲報錯且**絕不標刪來源**（安全鐵則）
    server = fresh_server(supports_move=False)                   # 強制走 copy fallback
    server.arm_response("COPY", code="OVERQUOTA", text="Mailbox is over quota")
    client = connected_client(monkeypatch, server)
    with pytest.raises(BackendError):
        client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert INBOX_NEWSLETTER_UID in {u for u, _ in server.snapshot()["INBOX"]}
    assert server.command_count("UID STORE") == 0 and server.command_count("UID EXPUNGE") == 0


# ── E5：非預期/畸形行容錯 + 損毀 MIME 容錯 ─────────────────────────────────────

def test_unsolicited_alert_line_tolerated_by_real_imaplib():
    server = ImapServer({"INBOX": [message(10, "Hello")]})
    server.arm_unsolicited("FETCH", line=b"* OK [ALERT] Scheduled maintenance soon")
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    typ, data = m.uid("fetch", "10", "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
    assert typ == "OK" and b"Subject: Hello" in data[0][1]       # parser 容忍非預期行、資料仍正確
    assert m.untagged_responses.get("OK")                        # ALERT 被記下，未崩
    assert any(fe["kind"] == "unsolicited" for fe in server.fault_events)


def test_corrupted_encoded_word_does_not_crash_product(monkeypatch):
    # 損毀 MIME：主旨是壞掉的 encoded-word（非法 base64）→ 產品 _decode 容錯、不崩潰
    server = ImapServer({"INBOX": [message(10, "=?UTF-8?B?@@@not_base64@@@?=")]})
    headers = connected_client(monkeypatch, server).list_headers("INBOX")
    assert len(headers) == 1 and headers[0].uid == "10"          # 仍產出一列、不丟例外


# ── E7：非同步 EXPUNGE 推送（in-flight + 真實移除）──────────────────────────────

def test_async_expunge_during_noop_pushes_untagged_and_removes():
    server = ImapServer({"INBOX": [message(10, "A"), message(20, "B")]})
    server.arm_async_expunge(10, before_op="NOOP")              # 他處刪除 uid 10，於下個 NOOP 推送
    m = _imap_over(server)
    m.select("INBOX")
    m.noop()
    assert m.untagged_responses.get("EXPUNGE") == [b"1"]        # 真 imaplib 收到 * 1 EXPUNGE
    assert {x.uid for x in server.mailboxes["INBOX"]} == {20}   # 引擎狀態真實移除
    assert any(fe["kind"] == "async_expunge" for fe in server.fault_events)


def test_async_expunge_multiple_renumbers_correctly():
    # 多封非同步刪除：移除 seq1(uid10) 後 uid30 由 seq3→seq2 → 推 * 1 EXPUNGE、* 2 EXPUNGE（重編序號）
    server = ImapServer({"INBOX": [message(10, "A"), message(20, "B"), message(30, "C")]})
    server.arm_async_expunge([10, 30], before_op="NOOP")
    m = _imap_over(server)
    m.select("INBOX")
    m.noop()
    assert m.untagged_responses.get("EXPUNGE") == [b"1", b"2"]  # 序號逐封重編
    assert {x.uid for x in server.mailboxes["INBOX"]} == {20}   # 只剩中間那封


# ── E8：強制狀態機（非法指令順序）──────────────────────────────────────────────

def test_state_enforcement_select_before_auth_is_bad():
    server = ImapServer({"INBOX": []})
    resp = server.feed(b"a1 SELECT INBOX")                      # 未認證即選取
    assert b"a1 BAD" in resp and b"CLIENTBUG" in resp
    assert server.commands("SELECT")[0].result_typ == "BAD"
    assert any(fe["kind"] == "state_violation" for fe in server.fault_events)


def test_state_enforcement_uid_before_select_is_bad():
    server = ImapServer({"INBOX": [message(10)]})
    server.feed(b"a1 AUTHENTICATE XOAUTH2")
    server.feed(base64.b64encode(b"x"))
    resp = server.feed(b"a2 UID FETCH 10 (UID)")               # 已認證但未選取
    assert b"a2 BAD" in resp
    assert any(fe["kind"] == "state_violation" and fe["op"] == "UID" for fe in server.fault_events)


def test_enforce_state_can_be_disabled():
    server = ImapServer({"INBOX": []}, enforce_state=False)
    resp = server.feed(b"a1 SELECT INBOX")                      # 關閉強制 → 不擋
    assert b"a1 OK" in resp


# ── E9：進階遙測（狀態軌跡 / 故障事件 / 合法性）────────────────────────────────

def test_state_transitions_recorded_and_legal(monkeypatch):
    server = fresh_server()
    connected_client(monkeypatch, server).list_headers("INBOX")
    server.assert_state_machine_legal()                         # 產品驅動的轉移皆合法
    seen = [t[1] for t in server.transitions]
    assert "AUTH" in seen and "SELECTED" in seen
    rep = server.loop_report()
    assert "state_transitions" in rep and "fault_events" in rep and "connections" in rep


def test_fault_events_capture_session_loss(monkeypatch):
    server = fresh_server()
    server.arm_expiry(before_op="move", nth=1, mode="eof")
    _no_sleep(monkeypatch)
    connected_client(monkeypatch, server, token_provider=lambda: "tok").move(
        str(INBOX_NEWSLETTER_UID), "Archive", "INBOX"
    )
    assert any(fe["kind"] == "session_loss" and fe["detail"] == "eof" for fe in server.fault_events)


# ── E10：序列對齊器 ──────────────────────────────────────────────────────────

def test_assert_sequence_subsequence_matches(monkeypatch):
    server = fresh_server()
    connected_client(monkeypatch, server).list_headers("INBOX")
    server.assert_sequence(["AUTHENTICATE", "EXAMINE", "UID SEARCH", "UID FETCH"])
    server.assert_sequence([("UID FETCH", "OK")])               # 命令 + 結果碼


def test_assert_sequence_detects_missing(monkeypatch):
    server = fresh_server()
    connected_client(monkeypatch, server).list_headers("INBOX")
    with pytest.raises(AssertionError):
        server.assert_sequence(["UID MOVE"])                    # 唯讀匯出沒有搬移


def test_assert_sequence_retry_then_success(monkeypatch):
    # 行為軌跡驗證：注入一次認證失敗 → 重連再認證 → 搬移成功（序列對齊）
    server = fresh_server()
    server.arm_expiry(before_op="move", nth=1, mode="authfail")
    _no_sleep(monkeypatch)
    connected_client(monkeypatch, server, token_provider=lambda: "tok").move(
        str(INBOX_NEWSLETTER_UID), "Archive", "INBOX"
    )
    server.assert_sequence(["AUTHENTICATE", "AUTHENTICATE", ("UID MOVE", "OK")])


# ── 確定性異常注入：不合規折行 ───────────────────────────────────────────────
# 背景：CI(3.10) 揭露舊折行把無空白長 token 折在欄名後 → 值落續行 → email 跨版本對前導折疊空白
# 處理不一。預設折行已修為 RFC 5322 保真；此處把「不合規折行」正式化為**確定性異常注入**，
# 用以驗證產品異常路徑能否穩健還原內容。確定性在引擎吐的 bytes（版本無關）；版本差異只在「產品
# 如何解讀」，故以內容比對（容忍前導空白）斷言。

def test_malformed_fold_emits_deterministic_noncompliant_wire():
    # 引擎確定性吐出不合規折行 bytes（欄名後立即折、值在續行）——無論 Python 版本皆同
    server = ImapServer({"INBOX": [message(10, "Quarterly Report")]}, malformed_fold=True)
    m = _imap_over(server)
    m.select("INBOX", readonly=True)
    literal = m.uid("fetch", "10", "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")[1][0][1]
    assert b"Subject:\r\n Quarterly Report" in literal           # 確定性：值整段在續行


def test_malformed_fold_product_recovers_content_version_independent(monkeypatch):
    # 產品異常路徑：對不合規折行仍穩健還原內容、不崩潰；斷言以內容比對 → 版本無關。
    #
    # ⚠ `.strip()` 是**承重的**，非美化（SR 條件）：不合規折行使值落在續行，產品 `_decode`/`_unfold`
    # 對「續行前導折疊空白」的處理隨 Python 版本而異——3.12 的 `email` 解析時已去除（→ "Quarterly
    # Report"），但 **3.10 會保留前導空白**（→ " Quarterly Report"），且產品 `_unfold` 只摺疊含換行的
    # 續行、不會去掉 email 已攤平後殘留的前導空白。故此處以 `.strip()` 後內容比對作版本無關斷言——
    # 它**驗證「內容正確還原、未崩潰、未漏字/亂序」**，但**刻意不**斷言前導空白的有無。
    # 這是**已知產品限制**（不合規折行下前導空白可能殘留）；正解屬產品強化（`_unfold`/`_decode`
    # 去前導折疊空白），記於 backlog [[roadmap-backlog]]，本（模擬器）分支不動產品碼。
    server = ImapServer(
        {"INBOX": [message(10, "Quarterly Report", "boss@x.com", "me@x.com", "Mon")]},
        malformed_fold=True,
    )
    headers = connected_client(monkeypatch, server).list_headers("INBOX")
    assert len(headers) == 1 and headers[0].uid == "10"
    assert headers[0].subject.strip() == "Quarterly Report"      # 內容正確還原（前導空白容忍，見上）
    assert headers[0].sender.strip() == "boss@x.com"
