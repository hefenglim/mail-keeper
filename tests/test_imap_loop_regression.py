"""Loop-regression 驗收 —— 大量郵件迴圈一律跑在 IMAP 模擬器上，並分析其 log 數據。

專案鐵則（CLAUDE.md §7）：任何「大量郵件 / 迴圈」行為的回歸測試都透過**線級引擎**執行，
再以引擎產出的 log 數據抓出冗餘與效能回歸：
  * ``fetches_per_folder`` / ``redundant_full_folder_reads`` —— 同一來源夾整夾標頭是否被重抓
    （冗餘下載；對照「不可冗餘重抓」鐵則）。
  * ``assert_all_fetches_request_uid`` —— 每個 FETCH 都索取 UID（釘死 0.5.x UID 全空回歸）。
  * ``command_counts`` / ``roundtrips`` / ``bytes_*`` —— 往返與流量瓶頸分析。
  * 雙層：``snapshot()`` 確認資料變動正確、他人 ``\\Deleted`` 不被波及。
"""
from __future__ import annotations

import imaplib

import pytest

from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_EMOJI_UID,
    INBOX_NEWSLETTER_UID,
    INBOX_QUOTED_FROM_UID,
    INBOX_USER_DELETED_UID,
    bulk_server,
    fresh_server,
)
from imap_server import ImapServer
from imap_sim import DELETED, message
from imap_transport import connected_client

from mailkeeper import classifier
from mailkeeper.csv_io import ClassificationRow


def _rows(*specs):
    return [ClassificationRow(*s) for s in specs]


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr("mailkeeper.imap_client.time.sleep", lambda s: None)


def test_full_simulation_regression_loop(monkeypatch):
    """端到端**全流程回歸**（單一模擬引擎）：連線 → 列夾 → 多列分類（多目標夾）→ 執行搬移
    （中途 token 過期 → 透明重連）→ 雙層驗證 + log 分析。一條測試走完整個產品迴圈。"""
    server = fresh_server()
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")

    # 1) 列夾（含 CJK/巢狀，經 mUTF-7）
    assert set(client.list_folders()) == {"INBOX", "Sent", "Archive", "Work/Projects", "台北", "R&D", "VIP客戶"}

    # 2) 多列分類（共用 cache、含不同目標夾）
    cache = classifier.ClassifyCache()
    rows = _rows(
        (str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"),
        (str(INBOX_CJK_UID), "INBOX", "Archive"),
        (str(INBOX_EMOJI_UID), "INBOX", "Archive"),
        (str(INBOX_QUOTED_FROM_UID), "INBOX", "Work/Projects"),
    )
    items = classifier.build_report(client, rows, cache=cache)

    # 3) 執行搬移，第 3 次 move 中途 token 過期 → 透明重連後從中斷處續完
    server.arm_expiry(before_op="move", nth=1, mode="eof")
    results = classifier.execute(client, items, cache=cache)

    # 第一層（log 分析）：全部成功、重連發生、整夾標頭零冗餘、UID 不變量、4 次搬移
    assert len(results) == 4 and all(r.ok for r in results)
    rep = server.loop_report()
    assert rep["authentications"] >= 2                                   # 中途重連
    assert rep["redundant_full_folder_reads"] == {}                      # 讀取迴圈零冗餘
    assert rep["fetches_per_folder"] == {}                               # P1：存在性改 UID SEARCH、零整夾標頭抓取
    assert rep["command_counts"].get("UID SEARCH") == 1                  # INBOX 現存查詢一次（重連後重用 cache）
    assert rep["command_counts"]["UID MOVE"] >= 2                        # 批次搬移（含重連重試該批）
    server.assert_all_fetches_request_uid()

    # 第二層（快照）：3 進 Archive、1 進 Work/Projects、他人 \Deleted(106) 不被波及、四封都離開 INBOX
    after = server.snapshot()
    assert len(server.mailboxes["Archive"]) == 3
    assert len(server.mailboxes["Work/Projects"]) == 2                   # 原 1 + 搬入 1
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in after["INBOX"]
    moved = {INBOX_NEWSLETTER_UID, INBOX_CJK_UID, INBOX_EMOJI_UID, INBOX_QUOTED_FROM_UID}
    assert moved.isdisjoint({u for u, _ in after["INBOX"]})


def test_bulk_classify_reads_each_source_folder_once(monkeypatch):
    # 大量分類（多列、同一來源夾）：整夾標頭只抓一次（報告讀、執行重用），LIST 只一次。
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    cache = classifier.ClassifyCache()
    rows = _rows(
        (str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"),
        (str(INBOX_CJK_UID), "INBOX", "Archive"),
        (str(INBOX_EMOJI_UID), "INBOX", "Archive"),
        (str(INBOX_QUOTED_FROM_UID), "INBOX", "Archive"),
    )
    items = classifier.build_report(client, rows, cache=cache)
    results = classifier.execute(client, items, cache=cache)
    assert len(results) == 4 and all(r.ok for r in results)

    rep = server.loop_report()
    # loop-regression 不變量（用 log 數據抓冗餘/回歸）：
    assert rep["redundant_full_folder_reads"] == {}            # 零冗餘
    assert rep["fetches_per_folder"] == {}                     # P1：存在性改 UID SEARCH、零整夾標頭抓取
    assert rep["command_counts"].get("UID SEARCH") == 1       # INBOX 現存查詢只一次（4 列共用快取）
    assert rep["command_counts"].get("LIST") == 1             # 資料夾清單只讀一次
    assert rep["command_counts"].get("UID MOVE") == 1         # 四列同 (INBOX→Archive) 一批搬移
    server.assert_all_fetches_request_uid()                    # 每個 FETCH 都索取 UID

    # 第二層：四封進 Archive、他人 \Deleted(106) 全程不被波及
    assert len(server.mailboxes["Archive"]) == 4
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in server.snapshot()["INBOX"]


def test_multibatch_fetch_over_100_messages_drives_progress(monkeypatch):
    # >100 封 → 產品 _FETCH_BATCH=50 分批：120 封 = 50+50+20 = 3 批 UID FETCH，進度逐批前進
    server = bulk_server(120)
    client = connected_client(monkeypatch, server)
    progress: list[tuple[int, int]] = []
    headers = client.list_headers("INBOX", on_progress=lambda d, t: progress.append((d, t)))
    assert len(headers) == 120 and all(h.uid for h in headers)  # 全部、UID 全非空
    assert server.command_count("UID FETCH") == 3               # 多批
    server.assert_all_fetches_request_uid()
    assert progress[0] == (50, 120) and progress[-1] == (120, 120)  # 50 → 100 → 120
    # 多批路徑也正確解碼 CJK encoded-word
    assert any(h.subject == "批量信件 CJK" for h in headers)


def test_list_headers_resumable_skips_fetched_batches(monkeypatch):
    # feature 008 (P5)：分批讀標頭中途斷線 → **續抓**（已取得批次不重抓），非整批重抓。
    server = bulk_server(120)  # 預設 batch 50 → 3 批
    server.arm_expiry(before_op="fetch", nth=2, mode="eof")  # 第 2 批前斷線
    _no_sleep(monkeypatch)
    progress: list[tuple[int, int]] = []
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    headers = client.list_headers("INBOX", on_progress=lambda d, t: progress.append((d, t)))
    assert len(headers) == 120 and all(h.uid for h in headers)        # 完整、UID 全非空
    assert len({h.uid for h in headers}) == 120                        # 無重複/遺漏
    server.assert_all_fetches_request_uid()
    # 續抓的決定性證明（SR C1）：每個 UID 至多被抓一次——整批重抓會重抓前段 UID → 此斷言失敗。
    fetched: list[int] = []
    for cmd in server.commands("UID FETCH"):
        fetched.extend(cmd.affected_uids)
    assert len(fetched) == len(set(fetched)) == 120                    # 無重抓、無遺漏：120 封各抓一次
    assert server.command_count("UID FETCH") == 3                      # ⌈120/50⌉（eof 失敗批未計）；整批重抓會是 4
    assert server.command_count("AUTHENTICATE") >= 2                   # 發生重連
    # 進度跨重連嚴格遞增、不重複回報（整批重抓會重覆出現 (50,120)）：
    assert progress == [(50, 120), (100, 120), (120, 120)]


def test_list_headers_uidvalidity_change_on_reconnect_refetches(monkeypatch):
    # feature 008 (FR-002)：重連後 UIDVALIDITY 變更（信箱重建）→ 捨棄過時進度、重新 SEARCH 安全重抓。
    server = bulk_server(60)  # batch 50 → 2 批
    server.arm_expiry(before_op="fetch", nth=2, mode="eof")
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    orig_reconnect = client._reconnect
    def reconnect_then_rebuild() -> None:
        orig_reconnect()
        server.set_uidvalidity("INBOX", 999999)  # 斷線期間信箱被重建 → UIDVALIDITY 變更
    monkeypatch.setattr(client, "_reconnect", reconnect_then_rebuild)
    headers = client.list_headers("INBOX")
    assert len(headers) == 60 and all(h.uid for h in headers)   # 安全重抓後完整、UID 全非空
    assert len({h.uid for h in headers}) == 60                   # 無重複
    assert server.command_count("UID SEARCH") >= 2               # 變更後重新查現存 UID（未沿用過時進度）
    assert server.command_count("AUTHENTICATE") >= 2


def test_list_headers_parse_equivalent_decodes_master(monkeypatch):
    # feature 008 (P7)：header-only 解析（BytesHeaderParser）輸出與優化前逐字一致——
    # 涵蓋 CJK / emoji / encoded-word 顯示名 / 空主旨。
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    by_uid = {h.uid: h for h in client.list_headers("INBOX")}
    assert by_uid["102"].subject == "週報 Q1 報告"                     # CJK
    assert by_uid["103"].subject == "🎉 Happy New Year 新年快樂"       # emoji + CJK
    assert by_uid["104"].subject == "FW: 推薦職務"                     # CJK
    assert by_uid["101"].subject == "Weekly Newsletter"               # ASCII
    assert by_uid["107"].subject == ""                                # 空主旨
    assert by_uid["102"].sender == "王經理 <boss@x.com>"              # encoded-word 顯示名


def test_list_headers_fetch_batch_size_controls_roundtrips(monkeypatch):
    # feature 008 (P6)：fetch_batch_size 控制每批封數 → UID FETCH 往返=⌈N/M⌉。
    server = bulk_server(25)
    client = connected_client(monkeypatch, server, fetch_batch_size=10)
    headers = client.list_headers("INBOX")
    assert len(headers) == 25 and all(h.uid for h in headers)
    assert server.command_count("UID FETCH") == 3  # ⌈25/10⌉


def test_list_headers_reconnect_exhausted_raises(monkeypatch):
    # feature 008：重連用盡仍失敗 → **如實外拋**（不靜默回傳不完整標頭）。
    server = bulk_server(120)
    server.arm_expiry(before_op="fetch", nth=2, mode="eof", persist=True)  # 第 2 批起每次都斷
    _no_sleep(monkeypatch)
    client = connected_client(
        monkeypatch, server, token_provider=lambda: "tok", max_reconnect_attempts=2
    )
    with pytest.raises(imaplib.IMAP4.abort):
        client.list_headers("INBOX")


def test_list_headers_reconnect_status_is_secret_free(monkeypatch):
    # feature 008 (FR-009/G2)：續傳路徑重連期間的狀態訊息不含 token/secret。
    server = bulk_server(60)
    server.arm_expiry(before_op="fetch", nth=2, mode="eof")
    _no_sleep(monkeypatch)
    statuses: list[str] = []
    client = connected_client(
        monkeypatch, server, token_provider=lambda: "super-secret-token", on_status=statuses.append
    )
    client.list_headers("INBOX")
    assert any("重新連線" in s for s in statuses)                       # 恢復期間有狀態
    assert all("super-secret-token" not in s for s in statuses)        # 狀態絕不含 token


def test_select_cjk_folder_encodes_mutf7(monkeypatch):
    # F1：CJK 夾名（台北）的 select 不再 UnicodeEncodeError —— 產品經 _mailbox_arg（mUTF-7 + 引號）外送。
    server = fresh_server()  # 母版含 台北（uid 401）
    client = connected_client(monkeypatch, server)
    assert client.list_uids("台北") == {"401"}


def test_move_into_spaced_folder_quotes_name(monkeypatch):
    # F2：含空白夾名（如 Outlook 內建 'Junk Email'）正確加引號 → 引擎/真伺服器接受、搬移成功、不誤搬。
    server = ImapServer({
        "INBOX": [message(101, "x"), message(106, "d", flags={DELETED})],
        "Junk Email": [],
        "Junk": [],   # 陷阱：若未加引號，'UID MOVE 101 Junk Email' 在寬鬆伺服器可能誤搬到既有 'Junk'
    })
    client = connected_client(monkeypatch, server)
    out = client.move_many(["101"], "Junk Email", "INBOX")
    assert out == {"101": None}
    assert len(server.mailboxes["Junk Email"]) == 1 and len(server.mailboxes["Junk"]) == 0
    assert 101 not in {m.uid for m in server.mailboxes["INBOX"]}
    assert 106 in {m.uid for m in server.mailboxes["INBOX"]}  # 他人 \Deleted 不被波及


def test_move_loop_avoids_redundant_select(monkeypatch):
    # feature 007 (P3/C2)：分類迴圈對同來源夾連續搬移，靠 _ensure_selected 不重複 SELECT
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    cache = classifier.ClassifyCache()
    rows = _rows(
        (str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"),
        (str(INBOX_CJK_UID), "INBOX", "Archive"),
        (str(INBOX_EMOJI_UID), "INBOX", "Archive"),
    )
    items = classifier.build_report(client, rows, cache=cache)
    classifier.execute(client, items, cache=cache)
    # 同夾同模式不重選：3 封搬移只首次 SELECT INBOX（讀寫），其餘跳過
    assert server.redundant_selects() == 0
    bn = server.bottleneck()
    assert bn["redundant_selects"] == 0 and bn["redundant_full_folder_reads"] == {}


def test_redundant_refetch_would_be_caught_by_log(monkeypatch):
    # 反向證明：若不共用快取（兩趟各自整夾掃描），log 立刻顯示同夾被抓兩次 → 冗餘可被抓出。
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    rows = _rows((str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"))
    classifier.build_report(client, rows, cache=classifier.ClassifyCache())   # cache A
    classifier.execute(
        client, classifier.build_report(client, rows, cache=classifier.ClassifyCache()),
        cache=classifier.ClassifyCache(),                                     # cache B（不共用）
    )
    rep = server.loop_report()
    # P1 後存在性走 UID SEARCH（非整夾 FETCH）：不共用快取 → 同夾被重複 SEARCH，log 仍可抓出冗餘
    assert rep["command_counts"]["UID SEARCH"] >= 2               # 同夾現存查詢被重複執行


def test_loop_report_exposes_valuable_log_data(monkeypatch):
    # 確認引擎輸出的 log 分析數據面齊全（供人工核對 / 瓶頸分析），且 dump() 可一次定位。
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    client.list_headers("INBOX")
    rep = server.loop_report()
    for key in (
        "roundtrips", "bytes_in", "bytes_out", "command_counts",
        "fetches_per_folder", "redundant_full_folder_reads",
        "authentications", "destructive_ops",
    ):
        assert key in rep
    assert rep["roundtrips"] >= 1 and rep["bytes_out"] > 0
    assert rep["authentications"] == 1 and rep["destructive_ops"] == 0  # 唯讀匯出：零破壞性
    d = server.dump()
    assert "structured log" in d and "snapshot" in d and "wire" in d


# ── feature 006 (P1)：list_uids 存在性查詢——只查 UID、不抓整夾標頭 ──────────────

def test_list_uids_uses_search_not_whole_folder_header_fetch(monkeypatch):
    # P1/FR-001/SC-001：list_uids 只送 UID SEARCH ALL，完全不對整夾抓完整標頭
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    uids = client.list_uids("INBOX")
    assert uids == {str(u) for u in range(101, 109)}            # 母版 INBOX 8 封
    cc = server.loop_report()["command_counts"]
    assert cc.get("UID SEARCH", 0) >= 1                          # 用 SEARCH 取得存在性
    assert server.command_count("UID FETCH") == 0               # 完全不抓標頭


def test_list_uids_includes_deleted_not_expunged(monkeypatch):
    # Clarify Q1：已標 \Deleted 未 expunge 仍算「現存」（與現況 SEARCH ALL 一致）
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    assert str(INBOX_USER_DELETED_UID) in client.list_uids("INBOX")


def test_list_uids_returns_empty_set_on_search_no(monkeypatch):
    # 防禦分支（SR F1）：伺服器對 UID SEARCH 回 NO → list_uids 回空集合、不崩潰
    server = fresh_server()
    server.arm_response("search", typ="NO")
    client = connected_client(monkeypatch, server)
    assert client.list_uids("INBOX") == set()


def test_list_uids_reconnects_and_returns_full_set(monkeypatch):
    # FR-009：查詢期間連線中斷 → 透明重連後仍回完整集合、不遺漏
    server = fresh_server()
    _no_sleep(monkeypatch)
    server.arm_expiry(before_op="search", nth=1, mode="eof")
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    uids = client.list_uids("INBOX")
    assert uids == {str(u) for u in range(101, 109)}
    assert server.loop_report()["authentications"] >= 2          # 中途重連發生


def test_list_uids_downloads_far_less_than_list_headers(monkeypatch):
    # SC-003：同夾 list_uids 的下行位元組遠低於 list_headers（整夾標頭）→ 降幅 ≥90%
    s1 = bulk_server(200)
    connected_client(monkeypatch, s1).list_uids("INBOX")
    uids_bytes = s1.loop_report()["bytes_out"]

    s2 = bulk_server(200)
    connected_client(monkeypatch, s2).list_headers("INBOX")
    headers_bytes = s2.loop_report()["bytes_out"]

    assert uids_bytes < headers_bytes * 0.1                      # 至少降 90%


def test_build_report_existence_uses_search_no_header_fetch(monkeypatch):
    # P1 端到端（真 client over 引擎）：報告階段以 UID SEARCH 判存在性、零整夾標頭抓取（SC-001）
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    cache = classifier.ClassifyCache()
    rows = _rows(
        (str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"),
        (str(INBOX_USER_DELETED_UID), "INBOX", "Archive"),   # 已標 \Deleted → 視為存在=candidate（Q1）
        ("999999", "INBOX", "Archive"),                       # 不存在 → infeasible
    )
    items = classifier.build_report(client, rows, cache=cache)
    status = {it.row.uid: it.status for it in items}
    assert status[str(INBOX_NEWSLETTER_UID)] == classifier.CANDIDATE
    assert status[str(INBOX_USER_DELETED_UID)] == classifier.CANDIDATE   # Clarify Q1
    assert status["999999"] == classifier.INFEASIBLE
    rep = server.loop_report()
    assert rep["command_counts"].get("UID SEARCH", 0) >= 1
    assert server.command_count("UID FETCH") == 0               # 報告階段零整夾標頭抓取（SC-001）
    assert rep["fetches_per_folder"] == {}                      # 無整夾 header 讀


def test_list_headers_still_full_headers_not_minimized(monkeypatch):
    # US2/FR-006/SC-005：內容路徑**不得被誤最小化**——list_headers 仍取完整標頭、仍做 FETCH
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    headers = client.list_headers("INBOX")
    assert len(headers) == 8                                     # 母版 INBOX 8 封
    assert any("報告" in h.subject for h in headers)            # CJK encoded-word 主旨仍解碼（有內容）
    assert server.command_count("UID FETCH") >= 1               # 內容路徑仍做標頭 FETCH（未被最小化）
    assert server.command_count("UID SEARCH") >= 1              # 仍先 SEARCH 取 UID 再分批 FETCH


def test_move_many_batches_uid_move(monkeypatch):
    # feature 007 (P2)：move_many 同群以單一 UID MOVE 批次、免重複 SELECT
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    before = len(server.mailboxes["Archive"])
    uids = [str(u) for u in range(101, 109)]                     # INBOX 8 封
    out = client.move_many(uids, "Archive", "INBOX")
    assert out == {u: None for u in uids}
    assert server.command_count("UID MOVE") == 1                 # 8 封一批
    assert server.redundant_selects() == 0
    assert len(server.mailboxes["Archive"]) == before + 8 and server.mailboxes["INBOX"] == []


def test_move_many_chunks_at_cap(monkeypatch):
    # feature 007 (FR-014)：超過 MOVE_BATCH_MAX 分塊為多批
    monkeypatch.setattr("mailkeeper.config.MOVE_BATCH_MAX", 3)
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    client.move_many([str(u) for u in range(101, 109)], "Archive", "INBOX")  # 8 封 → ⌈8/3⌉=3 批
    assert server.command_count("UID MOVE") == 3
