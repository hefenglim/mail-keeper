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
from imap_sim import DELETED, FLAGGED, SEEN
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


@pytest.mark.xfail(
    reason="已知產品限制（SR C1）：fallback move 於 COPY 成功後、UID EXPUNGE 前斷線，重試會重做 "
    "COPY → 目標夾出現重複複本（非資料遺失，來源仍正確移除）。正解需重試前偵測既有複本或改用更原子序列；"
    "見 roadmap-backlog。本測試以期望（修好後）行為 xfail 記錄此限制，P3 遷移 fallback 路徑前應先修。",
    strict=False,
)
def test_fallback_move_idempotency_across_copy_known_limitation(monkeypatch):
    server = _server(supports_move=False)
    server.arm_expiry(before_op="EXPUNGE", nth=1, mode="eof")  # COPY 成功、UID EXPUNGE 前斷線
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    client.move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")
    assert INBOX_NEWSLETTER_UID not in {m.uid for m in server.mailboxes["INBOX"]}  # 來源確實移除（非遺失）
    assert len(server.mailboxes["Archive"]) == 1  # 期望恰一封；現況為 2（重試重複 COPY）→ 暫 xfail


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


def test_list_headers_eof_mid_fetch_refetches_whole(monkeypatch):
    server = _server()
    server.arm_expiry(before_op="fetch", nth=1, mode="eof")  # 標頭下載中途失效 → 整批重抓
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    headers = client.list_headers("INBOX")
    assert len(headers) == 8 and all(h.uid for h in headers)  # 重抓後完整、UID 全非空
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
