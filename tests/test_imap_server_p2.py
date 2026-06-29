"""線級 IMAP 引擎 P2 驗收 —— 破壞性命令 + 傳輸層失效注入（皆走真 imaplib over 引擎）。

涵蓋：
  * **破壞性命令**：UID MOVE（主路徑）、copy→store(+\\Deleted)→UID EXPUNGE（fallback）、
    COPY 失敗不誤刪、CREATE 冪等、STORE \\Seen/\\Flagged。每項雙層驗證（命令 log + 快照）。
  * **失效注入（headline）**：一套傳輸/協定層注入即覆蓋產品 ``_is_session_lost`` 的**全部**真實入口——
    EOF（token 過期主路徑）、OSError、ssl.SSLError、``* BYE``、tagged ``BAD [AUTHENTICATIONFAILED]``。
    其中 OSError / SSLError / AUTHENTICATIONFAILED 為**先前（FakeIMAPConn 時代）未測**的分支。
  * **資料安全鐵則**：搬移/重連全程，他人已標 ``\\Deleted`` 的郵件（母版 106）絕不被波及。
  * **byte 層保真**：STORE / UID EXPUNGE 的回應 wire 經真 imaplib（imaplib_probe）解析正確
    （SR 對 P2 的明確要求：每個新回應先對拍真 imaplib 再採信）。
"""
from __future__ import annotations

import base64
import imaplib

import pytest

from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_NEWSLETTER_UID,
    INBOX_USER_DELETED_UID,
    master_mailboxes,
)
from imap_server import ImapServer
from imap_sim import DELETED, FLAGGED, SEEN, message
from imap_transport import connected_client
from imaplib_probe import ScriptedIMAP4

from mailkeeper.imap_client import BackendError, ReauthRequired


def _server(**kw) -> ImapServer:
    return ImapServer(master_mailboxes(), **kw)


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr("mailkeeper.imap_client.time.sleep", lambda s: None)


# ── 破壞性命令（雙層驗證：命令 log + 快照）─────────────────────────────────

def test_move_happy_uid_move_only_target_two_layer(monkeypatch):
    server = _server()
    before = server.snapshot()
    connected_client(monkeypatch, server).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")

    # 第一層：用 UID MOVE、可寫 SELECT、非整夾 EXPUNGE
    assert server.command_count("UID MOVE") == 1 and server.command_count("EXPUNGE") == 0
    mv = server.commands("UID MOVE")[0]
    assert mv.args[1] == "Archive" and mv.affected_uids == (INBOX_NEWSLETTER_UID,)
    assert server.commands("SELECT")[-1].response_code == "READ-WRITE"

    # 第二層：只少了目標那封、Archive +1、他夾不動、他人 \Deleted 原封不動
    after = server.snapshot()
    assert {u for u, _ in before["INBOX"]} - {u for u, _ in after["INBOX"]} == {INBOX_NEWSLETTER_UID}
    assert len(after["Archive"]) == 1
    for box in ("Sent", "Work/Projects", "台北"):
        assert before[box] == after[box]
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in after["INBOX"]


def test_move_fallback_copy_store_uidexpunge_spares_foreign_deleted(monkeypatch):
    server = _server(supports_move=False)
    connected_client(monkeypatch, server).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")

    # 第一層：安全序列 copy → store(+\Deleted) → UID EXPUNGE（限定該封，非整夾 EXPUNGE）
    assert server.command_count("UID COPY") == 1
    store = server.commands("UID STORE")[0]
    assert store.args[1] == "+FLAGS" and "Deleted" in store.args[2]
    assert server.command_count("UID EXPUNGE") == 1 and server.command_count("EXPUNGE") == 0

    # 第二層：目標搬走、他人 \Deleted(106) 未被誤清、Archive +1
    inbox = {u for u, _ in server.snapshot()["INBOX"]}
    assert INBOX_NEWSLETTER_UID not in inbox and INBOX_USER_DELETED_UID in inbox
    assert len(server.mailboxes["Archive"]) == 1


def test_move_fallback_copy_fails_keeps_source(monkeypatch):
    server = _server(supports_move=False)
    client = connected_client(monkeypatch, server)
    with pytest.raises(BackendError):
        client.move(str(INBOX_NEWSLETTER_UID), "NoSuchFolder", "INBOX")
    # COPY 失敗（目標夾不存在）→ 絕不標刪/EXPUNGE，來源郵件須留存
    assert INBOX_NEWSLETTER_UID in {u for u, _ in server.snapshot()["INBOX"]}
    assert server.command_count("UID STORE") == 0
    assert server.command_count("UID EXPUNGE") == 0 and server.command_count("EXPUNGE") == 0
    # byte 層保真（SR C2）：真 imaplib 解析了 NO 回應的 [TRYCREATE] 響應碼
    assert client._imap.untagged_responses.get("TRYCREATE") is not None


def test_fallback_move_idempotent_across_copy_expunge_window(monkeypatch):
    # feature 007 (C1)：fallback 於「COPY 成功、UID EXPUNGE 前」斷線重試 → 以目標 Message-ID 去重，恰一封
    server = _server(supports_move=False)
    server.arm_expiry(before_op="EXPUNGE", nth=1, mode="eof")  # COPY+store 完成、UID EXPUNGE 前斷線
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert INBOX_NEWSLETTER_UID not in {m.uid for m in server.mailboxes["INBOX"]}  # 來源確實移除
    assert len(server.mailboxes["Archive"]) == 1  # 不重複複本（C1 修復）


def test_fallback_move_idempotent_across_copy_store_window(monkeypatch):
    # feature 007 (C1，更難子窗口)：「COPY 成功、標刪(store) 前」斷線——僅靠來源 \Deleted 旗標無法偵測，
    # 必以目標夾 Message-ID 去重才不重複。重試後目標恰一封。
    server = _server(supports_move=False)
    server.arm_expiry(before_op="STORE", nth=1, mode="eof")  # COPY 完成、標刪前斷線
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert INBOX_NEWSLETTER_UID not in {m.uid for m in server.mailboxes["INBOX"]}
    assert len(server.mailboxes["Archive"]) == 1  # Message-ID 去重覆蓋此窗口


def test_move_reconnect_mid_move_no_dup_no_loss(monkeypatch):
    # feature 007 (US2)：主路徑批次 UID MOVE 搬移中途斷線 → 透明重連續完、0 重複/0 遺漏、不波及他人 \Deleted
    server = _server()  # supports_move=True
    server.arm_expiry(before_op="move", nth=1, mode="eof")
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    client.move_many([str(INBOX_NEWSLETTER_UID), str(INBOX_CJK_UID)], "Archive", "INBOX")
    inbox = {u for u, _ in server.snapshot()["INBOX"]}
    assert INBOX_NEWSLETTER_UID not in inbox and INBOX_CJK_UID not in inbox
    assert len(server.mailboxes["Archive"]) == 2  # 兩封各一份、無重複
    assert server.loop_report()["authentications"] >= 2  # 發生重連
    assert INBOX_USER_DELETED_UID in inbox  # 他人 \Deleted 不被波及


def test_move_many_fallback_per_uid_spares_foreign_deleted(monkeypatch):
    # feature 007 US3：批次（無 UID MOVE）退逐封 copy 路徑——多封皆搬、他人 \Deleted(106) 不被波及、結果皆成功
    server = _server(supports_move=False)
    client = connected_client(monkeypatch, server)
    out = client.move_many([str(INBOX_NEWSLETTER_UID), str(INBOX_CJK_UID)], "Archive", "INBOX")
    assert out == {str(INBOX_NEWSLETTER_UID): None, str(INBOX_CJK_UID): None}
    assert server.command_count("UID COPY") == 2 and server.command_count("EXPUNGE") == 0
    inbox = {u for u, _ in server.snapshot()["INBOX"]}
    assert INBOX_NEWSLETTER_UID not in inbox and INBOX_CJK_UID not in inbox
    assert INBOX_USER_DELETED_UID in inbox           # 他人 \Deleted 不被波及
    assert len(server.mailboxes["Archive"]) == 2


def test_move_many_batch_bad_attributes_per_uid_not_dropped(monkeypatch):
    # SR F1：批次 UID MOVE 回 BAD（非連線類，imaplib 拋出）→ 退逐封歸因；錯誤如實記錄、不靜默丟棄/搬走
    server = _server()  # supports_move=True
    server.arm_response("MOVE", typ="BAD", persist=True)  # 每次 MOVE 都 BAD
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server)
    out = client.move_many([str(INBOX_NEWSLETTER_UID), str(INBOX_CJK_UID)], "Archive", "INBOX")
    assert set(out) == {str(INBOX_NEWSLETTER_UID), str(INBOX_CJK_UID)}
    assert all(v is not None for v in out.values())   # 逐封歸因（皆有錯誤訊息），非靜默成功
    inbox = {u for u, _ in server.snapshot()["INBOX"]}
    assert INBOX_NEWSLETTER_UID in inbox and INBOX_CJK_UID in inbox  # 未誤搬、來源留存


def test_fallback_no_uidplus_refuses_collateral_whole_expunge(monkeypatch):
    # SR F5：無 MOVE 又無 UIDPLUS，且來源夾尚有他人 \Deleted → 拒絕整夾 EXPUNGE（大聲失敗、不連坐）。
    server = _server(supports_move=False, supports_uidplus=False)
    client = connected_client(monkeypatch, server)
    with pytest.raises(BackendError):
        client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    inbox = {u for u, _ in server.snapshot()["INBOX"]}
    assert INBOX_USER_DELETED_UID in inbox        # 他人 \Deleted(106) 未被連坐清除
    assert len(server.mailboxes["Archive"]) == 1  # COPY 已成功（資料未遺失）
    assert server.command_count("EXPUNGE") == 0   # 絕不執行整夾 EXPUNGE


def test_fallback_no_uidplus_whole_expunge_safe_when_alone(monkeypatch):
    # SR F5：無 UIDPLUS 但來源夾「只有這封」\Deleted → 整夾 EXPUNGE 安全 → 正常移除。
    server = ImapServer(
        {"INBOX": [message(101, "x")], "Archive": []},
        supports_move=False, supports_uidplus=False,
    )
    client = connected_client(monkeypatch, server)
    client.move("101", "Archive", "INBOX")
    assert 101 not in {m.uid for m in server.mailboxes["INBOX"]}
    assert len(server.mailboxes["Archive"]) == 1
    assert server.command_count("EXPUNGE") == 1   # 唯有安全情況才整夾 EXPUNGE


def test_ensure_folder_reconnects(monkeypatch):
    # SR F6：建立目標夾遇連線中斷 → 透明重連續完（與其他 op 一致，不再中止整個分類）。
    server = _server()
    server.arm_expiry(before_op="create", nth=1, mode="eof")
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    client.ensure_folder("NewBox")
    assert "NewBox" in server.mailboxes
    assert server.command_count("AUTHENTICATE") >= 2


def test_ensure_folder_creates_then_idempotent(monkeypatch):
    server = _server()
    client = connected_client(monkeypatch, server)
    client.ensure_folder("NewBox")
    assert "NewBox" in server.mailboxes
    client.ensure_folder("NewBox")  # 已存在 → 伺服器回 NO，產品忽略、不丟例外
    assert server.command_count("CREATE") == 2


def test_mark_read_and_flag_set_flags(monkeypatch):
    server = _server()
    client = connected_client(monkeypatch, server)
    client.mark_read(str(INBOX_NEWSLETTER_UID), "INBOX")
    client.flag(str(INBOX_CJK_UID), "INBOX")
    msgs = {m.uid: m for m in server.mailboxes["INBOX"]}
    assert SEEN in msgs[INBOX_NEWSLETTER_UID].flags and FLAGGED in msgs[INBOX_CJK_UID].flags


# ── 失效注入：覆蓋 _is_session_lost 的全部真實入口（皆透明恢復）──────────────

@pytest.mark.parametrize("mode", ["eof", "oserror", "sslerror", "bye", "authfail"])
def test_session_loss_mid_move_transparently_reconnects(monkeypatch, mode):
    # 一套注入、五種真實斷線型態：第一次 move 即失效 → 自動續期+重連 → 搬移完成、不誤動他人 \Deleted
    server = _server()
    server.arm_expiry(before_op="move", nth=1, mode=mode)
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")

    assert INBOX_NEWSLETTER_UID not in {u for u, _ in server.snapshot()["INBOX"]}  # 透明恢復後搬成功
    assert server.command_count("AUTHENTICATE") >= 2  # 初次 + 重連各一次認證
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in server.snapshot()["INBOX"]  # 106 未波及
    assert len(server.mailboxes["Archive"]) == 1


def test_reauth_required_clean_stops_move(monkeypatch):
    # 靜默續期不可行（provider 拋 ReauthRequired）→ move 乾淨停止外拋、未搬走（不退化互動登入）
    server = _server()
    server.arm_expiry(before_op="move", nth=1, mode="eof")

    def provider():
        raise ReauthRequired("re-login")

    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=provider)
    with pytest.raises(ReauthRequired):
        client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert INBOX_NEWSLETTER_UID in {m.uid for m in server.mailboxes["INBOX"]}  # 未搬走


def test_backoff_is_bounded_and_capped(monkeypatch):
    server = _server()
    client = connected_client(
        monkeypatch, server, token_provider=lambda: "tok",
        backoff_base_seconds=1.0, backoff_cap_seconds=4.0, max_reconnect_attempts=5,
    )
    sleeps: list[float] = []
    monkeypatch.setattr("mailkeeper.imap_client.time.sleep", lambda s: sleeps.append(s))
    server.arm_expiry(before_op="move", nth=1, mode="eof")  # 一次過期 → 一次重連
    client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert sleeps and all(s <= 4.0 for s in sleeps)  # 有退避且不超過封頂


def test_reconnect_exhausted_raises_and_preserves_source(monkeypatch):
    server = _server()
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok", max_reconnect_attempts=2)
    server.arm_expiry(before_op="move", nth=1, mode="eof", persist=True)  # 持續失效 → 重連用盡仍失敗
    with pytest.raises(imaplib.IMAP4.abort):
        client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert INBOX_NEWSLETTER_UID in {u for u, _ in server.snapshot()["INBOX"]}  # 乾淨停止、未搬走


def test_list_headers_reconnect_mid_fetch_completes(monkeypatch):
    # feature 008 (P5)：標頭下載中途連線失效 → 透明重連後續抓完成、結果完整。
    # 單批情形（8 封/批 50）續傳退化為重取該批；「已取得批次不重抓」的多批續傳見
    # test_imap_loop_regression::test_list_headers_resumable_skips_fetched_batches。
    server = _server()
    server.arm_expiry(before_op="fetch", nth=1, mode="eof")
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    headers = client.list_headers("INBOX")
    assert len(headers) == 8 and all(h.uid for h in headers)  # 續抓後完整、UID 全非空
    assert server.command_count("AUTHENTICATE") >= 2


def test_reconnect_status_is_secret_free(monkeypatch):
    server = _server()
    server.arm_expiry(before_op="move", nth=1, mode="eof")
    _no_sleep(monkeypatch)
    statuses: list[str] = []
    client = connected_client(
        monkeypatch, server, token_provider=lambda: "super-secret-token", on_status=statuses.append
    )
    client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert any("重新連線" in s for s in statuses)                  # 恢復期間有狀態（FR-009）
    assert all("super-secret-token" not in s for s in statuses)   # 狀態絕不含 token（FR-012）


# ── byte 層保真：新破壞性回應 wire 經真 imaplib 解析正確（SR 對 P2 的明確要求）──

def test_destructive_response_wire_parses_through_real_imaplib():
    server = _server()
    server.feed(b"a2 AUTHENTICATE XOAUTH2")
    server.feed(base64.b64encode(b"x"))
    server.feed(b"a3 SELECT INBOX")
    store_resp = server.feed(b"a4 UID STORE %d +FLAGS (\\Deleted)" % INBOX_NEWSLETTER_UID)
    exp_resp = server.feed(b"a5 UID EXPUNGE %d" % INBOX_NEWSLETTER_UID)

    # STORE：回應 untagged 為 * <seq> FETCH (FLAGS (...))，真 imaplib 解析為 ('OK', [b'1 (FLAGS (\\Deleted))'])
    store_wire = store_resp.split(b"a4 OK")[0]
    m1 = ScriptedIMAP4({"STORE": store_wire})
    m1.state = "SELECTED"
    typ, dat = m1.uid("STORE", str(INBOX_NEWSLETTER_UID), "+FLAGS", "(\\Deleted)")
    assert typ == "OK" and b"FLAGS" in dat[0] and b"Deleted" in dat[0]

    # UID EXPUNGE：回應 untagged 為 * <seq> EXPUNGE，真 imaplib 記到 untagged_responses['EXPUNGE']
    exp_wire = exp_resp.split(b"a5 OK")[0]
    m2 = ScriptedIMAP4({"EXPUNGE": exp_wire})
    m2.state = "SELECTED"
    assert m2.uid("EXPUNGE", str(INBOX_NEWSLETTER_UID))[0] == "OK"
    assert m2.untagged_responses.get("EXPUNGE") == [b"1"]  # 真 imaplib 解析出 * 1 EXPUNGE


def test_move_copyuid_response_code_parsed_by_real_imaplib(monkeypatch):
    # byte 層保真（SR C2）：真 imaplib 解析了 MOVE tagged 回應的 [COPYUID ...] 響應碼（非僅 typ==OK）
    server = _server()
    client = connected_client(monkeypatch, server)
    client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    copyuid = client._imap.untagged_responses.get("COPYUID")
    assert copyuid and str(INBOX_NEWSLETTER_UID).encode() in copyuid[0]


def test_uid_expunge_only_targets_given_deleted_uid(monkeypatch):
    # UID EXPUNGE 只清「指定且已標 \Deleted」者：母版 106 已自標刪，但未被指定 → 不清
    server = _server()
    client = connected_client(monkeypatch, server)
    before = {u for u, _ in server.snapshot()["INBOX"]}
    # 先把 101 標刪，再 UID EXPUNGE 101（經由 fallback 路徑會這樣做；此處直接驗證引擎語意）
    client.mark_read(str(INBOX_NEWSLETTER_UID), "INBOX")  # 任意可寫操作確保 SELECT INBOX
    server.feed(b"z1 UID STORE %d +FLAGS (\\Deleted)" % INBOX_NEWSLETTER_UID)
    server.feed(b"z2 UID EXPUNGE %d" % INBOX_NEWSLETTER_UID)
    after = {u for u, _ in server.snapshot()["INBOX"]}
    assert before - after == {INBOX_NEWSLETTER_UID}        # 只清 101
    assert INBOX_USER_DELETED_UID in after                 # 他人已標刪的 106 未被波及
