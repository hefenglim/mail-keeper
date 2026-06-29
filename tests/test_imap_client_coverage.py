"""imap_client.py 全路徑覆蓋 —— 補齊 happy-path 碰不到的罕見/防禦分支。

分兩層，嚴守 CLAUDE.md §7：
  * **Group A/B（跨 seam）**：一律透過線級 IMAP 引擎（真 imaplib over ImapServer）觸發，
    並雙層驗證（command log + snapshot）。引擎不足之處先擴引擎（加保真案例）再寫產品測試——
    本期擴充：母版字面 `&`/混合夾名、原始 LIST 行覆寫（畸形 mUTF-7/零行）、SELECT 省略
    UIDVALIDITY、無 Message-ID 郵件。
  * **Group C（防禦守衛）**：``ReauthRequired`` 只由 token_provider 於重連時產生，被包的 op
    本體正常呼叫永不丟它；這些 ``except ReauthRequired: raise`` 守衛與 ``_conn`` 未連線、
    ``_is_session_lost`` 末路、``_decode_chunk`` 回退等，結構上引擎產生不出來（硬塞＝偽造
    回應，§7 紅旗）。故以**領域層例外注入**的針對性單元測試覆蓋，不偽造任何 imaplib wire reply。
"""
from __future__ import annotations

import imaplib

import pytest

from imap_dataset import fresh_server
from imap_server import ImapServer
from imap_sim import message
from imap_transport import SimIMAP4_SSL, connected_client

from mailkeeper.imap_client import (
    OutlookIMAPClient,
    ReauthRequired,
    _decode_chunk,
)


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr("mailkeeper.imap_client.time.sleep", lambda s: None)


def _client(**kw) -> OutlookIMAPClient:
    """未連線的產品 client（供純單元、防禦守衛測試）。"""
    return OutlookIMAPClient("user@x.com", "tok", **kw)


def _imap_over(server: ImapServer):
    """連上引擎、已認證的真 imaplib 客戶端（AUTH）——供保真案例直接解析引擎 wire。"""
    m = SimIMAP4_SSL(server)
    m.authenticate("XOAUTH2", lambda _c: b"user=u\x01auth=Bearer t\x01\x01")
    return m


# ══════════════════════════════════════════════════════════════════════════
# Group A — modified-UTF-7 夾名編解碼（經引擎；母版已含 R&D / VIP客戶）
# ══════════════════════════════════════════════════════════════════════════

def test_list_folders_decodes_literal_amp_and_mixed_mutf7(monkeypatch):
    """list_folders 解出字面 '&'（R&D，'&-' 轉義）與 ASCII+CJK 混合（VIP客戶）夾名。
    驅動 _decode_mutf7 對 '&...-' 編碼段前後 ASCII 字元的逐字處理與 '&-'→'&' 分支。"""
    server = fresh_server()
    folders = connected_client(monkeypatch, server).list_folders()
    assert "R&D" in folders        # 字面 & → 引擎以 'R&-D' 送出 → 產品還原
    assert "VIP客戶" in folders     # 'VIP' 直出 + '客戶' 編碼段 → 逐字還原
    assert "台北" in folders        # 既有純 CJK 仍正確


def test_ensure_folder_with_ampersand_encodes_mutf7(monkeypatch):
    """ensure_folder 對含字面 '&' 的新夾名要 modified-UTF-7 編碼（'&'→'&-'）後外送，
    引擎才能正確還原並建立。驅動 _encode_mutf7 的 '&' 分支，並雙層驗證 round-trip。"""
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    before = server.snapshot()
    client.ensure_folder("Q&A")  # 母版無此夾 → CREATE OK
    after = server.snapshot()
    assert "Q&A" not in before and "Q&A" in after          # snapshot：夾被建立、名稱正確還原
    assert any(op.command == "CREATE" and op.mailbox == "Q&A" for op in server.log)  # log：引擎收到解碼後夾名


# ══════════════════════════════════════════════════════════════════════════
# Group B（含 E2 保真）— 畸形 mUTF-7 / 零 LIST 行
# ══════════════════════════════════════════════════════════════════════════

def test_engine_list_override_parsed_by_real_imaplib():
    """E2 保真：set_list_lines 覆寫的原始 LIST 內容須能被**真 imaplib** 正確解析；
    零行時真 imaplib `list()` 回 data=[None]（驅動產品略過空行的依據）。"""
    server = ImapServer({"INBOX": []})
    server.set_list_lines(['LIST (\\HasNoChildren) "/" "&AB"', 'LIST (\\HasNoChildren) "/" "&AQ-"'])
    typ, data = _imap_over(server).list()
    assert typ == "OK"
    payloads = [d for d in data if d]
    assert b'"&AB"' in payloads[0] and b'"&AQ-"' in payloads[1]

    server2 = ImapServer({"INBOX": []})
    server2.set_list_lines([])
    typ2, data2 = _imap_over(server2).list()
    assert typ2 == "OK" and data2 == [None]   # 零 * LIST 行 → 真 imaplib 回 [None]


def test_list_folders_tolerates_malformed_mutf7(monkeypatch):
    """畸形 mUTF-7 夾名（'&' 後無 '-'、'&...-' 內非法 base64）—— 產品須穩健容錯、原樣保留、不崩潰。
    驅動 _decode_mutf7 的兩條防禦分支（105-106 無收尾、115-116 base64/utf-16 失敗）。"""
    server = fresh_server()
    server.set_list_lines([
        'LIST (\\HasNoChildren) "/" "Inbox"',
        'LIST (\\HasNoChildren) "/" "&AB"',      # '&' 後無 '-' → 原樣保留（105-106）
        'LIST (\\HasNoChildren) "/" "&AQ-"',     # 合法 base64 但解出奇數位元組 → utf-16-be 失敗 → 原樣保留（115-116）
    ])
    folders = connected_client(monkeypatch, server).list_folders()
    assert folders == ["Inbox", "&AB", "&AQ-"]  # 三者皆保留、順序不變、無例外


def test_list_folders_empty_when_no_list_lines(monkeypatch):
    """LIST 回 OK 但無任何 '* LIST' 行（真 imaplib data=[None]）→ 產品略過空項、回空清單。
    驅動 _list_folders_impl 的 `if not line: continue`（306）。"""
    server = fresh_server()
    server.set_list_lines([])
    assert connected_client(monkeypatch, server).list_folders() == []


# ══════════════════════════════════════════════════════════════════════════
# Group B — list_headers 邊角：SEARCH 非 OK / 省略 UIDVALIDITY
# ══════════════════════════════════════════════════════════════════════════

def test_list_headers_returns_empty_when_search_not_ok(monkeypatch):
    """SEARCH 回 NO（非 OK）→ list_headers 回空清單（334）。"""
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    server.arm_response("SEARCH", typ="NO")  # 下一次 UID SEARCH ALL → NO
    assert client.list_headers("INBOX") == []


def test_list_headers_handles_missing_uidvalidity(monkeypatch):
    """E3：伺服器 SELECT 省略 [UIDVALIDITY] → _current_uidvalidity 回 None（390），
    list_headers 仍正常完整抓取（無 UIDVALIDITY 進度檢查、不崩潰）。"""
    server = fresh_server(send_uidvalidity=False)
    headers = connected_client(monkeypatch, server).list_headers("INBOX")
    assert [h.uid for h in headers] == [str(u) for u in range(101, 109)]


def test_engine_select_can_omit_uidvalidity():
    """E3 保真：send_uidvalidity=False 時真 imaplib SELECT 後 untagged_responses 無 UIDVALIDITY。"""
    server = fresh_server(send_uidvalidity=False)
    m = _imap_over(server)
    assert m.select("INBOX", readonly=True)[0] == "OK"
    assert "UIDVALIDITY" not in m.untagged_responses


# ══════════════════════════════════════════════════════════════════════════
# Group B — 後備搬移（supports_move=False）的冪等/退化/中斷分支
# ══════════════════════════════════════════════════════════════════════════

def test_fallback_move_idempotent_when_uid_already_gone(monkeypatch):
    """後備搬移冪等快路徑：來源已無此 uid（前次已搬走）→ 直接 no-op return（451）。
    不重複 COPY、目標夾不出現第二份複本。"""
    server = fresh_server(supports_move=False)
    client = connected_client(monkeypatch, server)
    client.move("101", "Archive", "INBOX")          # 第一次：經後備 copy→標刪→UID EXPUNGE
    arch_after_first = len(server.mailboxes["Archive"])
    client.move("101", "Archive", "INBOX")          # 第二次：來源已無 101 → 451 no-op
    assert len(server.mailboxes["Archive"]) == arch_after_first == 1   # 仍只有一份
    assert "101" not in {str(m.uid) for m in server.mailboxes["INBOX"]}
    assert server.command_count("UID COPY") == 1     # 第二次未再 COPY（冪等）


def test_fallback_move_without_message_id(monkeypatch):
    """無 Message-ID 的郵件走後備搬移：_message_id 回 None（492）→ _dest_has_copy 回 False（499）
    → 盡力 COPY 完成搬移。"""
    server = ImapServer(
        {"INBOX": [message(900, "no mid", "x@y.com", "me@outlook.my", "Mon", message_id=None)],
         "Archive": []},
        supports_move=False,
    )
    client = connected_client(monkeypatch, server)
    client.move("900", "Archive", "INBOX")
    assert len(server.mailboxes["Archive"]) == 1
    assert "900" not in {str(m.uid) for m in server.mailboxes["INBOX"]}


def test_fallback_move_when_message_id_fetch_fails(monkeypatch):
    """後備搬移時 MESSAGE-ID 的 FETCH 回非 OK → _message_id 回 None（486）→ 仍盡力 COPY 完成搬移。"""
    server = fresh_server(supports_move=False)
    client = connected_client(monkeypatch, server)
    server.arm_response("FETCH", typ="NO")  # _message_id 的 FETCH → NO
    client.move("101", "Archive", "INBOX")
    assert "101" not in {str(m.uid) for m in server.mailboxes["INBOX"]}
    assert len(server.mailboxes["Archive"]) == 1


def test_move_many_reconnects_when_per_uid_hits_session_loss(monkeypatch):
    """move_many 退逐封後，某封 _move_impl 於 COPY 中途連線中斷 → 重拋給外層 _with_reconnect
    重連重試（554）。最終全數成功、發生過一次重連。"""
    _no_sleep(monkeypatch)
    server = fresh_server(supports_move=False)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    server.arm_expiry(before_op="copy", mode="eof", nth=1)  # 第一封後備 COPY 時斷線
    results = client.move_many(["101", "102"], "Archive", "INBOX")
    assert results == {"101": None, "102": None}              # 重連後全數成功
    assert server.command_count("AUTHENTICATE") >= 2          # 首次 + 至少一次重連
    moved = {str(m.uid) for m in server.mailboxes["INBOX"]}
    assert "101" not in moved and "102" not in moved


def test_on_status_callback_exception_is_swallowed(monkeypatch):
    """on_status 回呼自身拋例外，絕不影響主流程（218-219）：重連時狀態提示失敗也照常完成讀取。"""
    _no_sleep(monkeypatch)

    def _boom(_msg: str) -> None:
        raise RuntimeError("status sink failed")

    server = fresh_server()
    client = connected_client(monkeypatch, server, on_status=_boom, token_provider=lambda: "tok")
    server.arm_expiry(before_op="search", mode="eof", nth=1)  # list_uids 的 SEARCH 斷線 → 觸發重連 → 呼叫 on_status
    uids = client.list_uids("INBOX")
    assert uids == {str(u) for u in range(101, 109)}           # on_status 爆掉仍順利完成


# ══════════════════════════════════════════════════════════════════════════
# Group C — 防禦守衛（領域層例外注入；不偽造 imaplib wire reply）
# ══════════════════════════════════════════════════════════════════════════

def test_decode_chunk_falls_back_to_utf8_replace(monkeypatch):
    """宣告字集解碼失敗、charset 偵測也回 None → 最末以 utf-8/replace 回退、永不拋例外（75）。"""
    class _NoMatch:
        def best(self):
            return None

    monkeypatch.setattr("mailkeeper.imap_client.charset_normalizer.from_bytes", lambda b: _NoMatch())
    out = _decode_chunk(b"\xff\xfe", "x-no-such-charset")
    assert out == b"\xff\xfe".decode("utf-8", "replace")


def test_is_session_lost_false_for_non_connection_error():
    """非連線/非 IMAP 協定例外（如 ValueError）→ _is_session_lost 回 False（228，不誤判為斷線）。"""
    assert OutlookIMAPClient._is_session_lost(ValueError("boom")) is False


def test_conn_property_raises_before_connect():
    """未連線即取用 _conn → RuntimeError（284），明確提示先 connect()。"""
    with pytest.raises(RuntimeError):
        _ = _client()._conn


def test_with_reconnect_reraises_reauth():
    """_with_reconnect 對 op 直接丟出的 ReauthRequired 立即外拋、不重試（252）。"""
    def _op():
        raise ReauthRequired("relogin")

    with pytest.raises(ReauthRequired):
        _client()._with_reconnect(_op)


def test_list_headers_reraises_reauth(monkeypatch):
    """list_headers 主體丟出 ReauthRequired → 立即外拋、不被當連線中斷重試（373）。"""
    server = fresh_server()
    client = connected_client(monkeypatch, server)

    def _raise(*a, **k):
        raise ReauthRequired("relogin")

    monkeypatch.setattr(client, "_ensure_selected", _raise)
    with pytest.raises(ReauthRequired):
        client.list_headers("INBOX")


def test_move_many_batch_reraises_reauth(monkeypatch):
    """move_many 批次 UID MOVE 丟出 ReauthRequired → 由 _move_many_impl 立即外拋（535）。"""
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    real_uid = client._imap.uid

    def _uid(cmd, *a):
        if cmd.lower() == "move":
            raise ReauthRequired("relogin")
        return real_uid(cmd, *a)

    monkeypatch.setattr(client._imap, "uid", _uid)
    with pytest.raises(ReauthRequired):
        client._move_many_impl(["101"], "Archive", "INBOX")


def test_move_many_per_uid_reraises_reauth(monkeypatch):
    """move_many 退逐封後，單封 _move_impl 丟出 ReauthRequired → 立即外拋（551）。
    批次 NO 由引擎（supports_move=False）忠實產生，只在產品方法注入領域例外。"""
    server = fresh_server(supports_move=False)
    client = connected_client(monkeypatch, server)

    def _raise(*a, **k):
        raise ReauthRequired("relogin")

    monkeypatch.setattr(client, "_move_impl", _raise)
    with pytest.raises(ReauthRequired):
        client._move_many_impl(["101"], "Archive", "INBOX")
