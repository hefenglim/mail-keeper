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

from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_EMOJI_UID,
    INBOX_NEWSLETTER_UID,
    INBOX_QUOTED_FROM_UID,
    INBOX_USER_DELETED_UID,
    bulk_server,
    fresh_server,
)
from imap_sim import DELETED
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
    assert set(client.list_folders()) == {"INBOX", "Sent", "Archive", "Work/Projects", "台北"}

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
    server.arm_expiry(before_op="move", nth=3, mode="eof")
    results = classifier.execute(client, items, cache=cache)

    # 第一層（log 分析）：全部成功、重連發生、整夾標頭零冗餘、UID 不變量、4 次搬移
    assert len(results) == 4 and all(r.ok for r in results)
    rep = server.loop_report()
    assert rep["authentications"] >= 2                                   # 中途重連
    assert rep["redundant_full_folder_reads"] == {}                      # 讀取迴圈零冗餘
    assert rep["fetches_per_folder"] == {"INBOX": 1}                     # INBOX 整夾只抓一次（重連後重用 cache）
    assert rep["command_counts"]["UID MOVE"] == 4                        # 4 封各成功搬移（失敗的那次未記錄）
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
    assert rep["redundant_full_folder_reads"] == {}            # 整夾標頭零冗餘重抓
    assert rep["fetches_per_folder"] == {"INBOX": 1}           # INBOX 整夾只抓一次（4 列共用快取）
    assert rep["command_counts"].get("LIST") == 1             # 資料夾清單只讀一次
    assert rep["command_counts"].get("UID MOVE") == 4         # 四列各一次搬移
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


def test_bottleneck_surfaces_redundant_select_waste(monkeypatch):
    # 分析助手深化：分類迴圈每封 move 前都重 SELECT 來源夾（同夾同模式）→ bottleneck 點出可省的重複
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
    # 3 封 → 3 次讀寫 SELECT INBOX，後 2 次是對「已選同模式」夾的重複（首次 EXAMINE→SELECT 模式切換不算）
    assert server.redundant_selects() == 2
    bn = server.bottleneck()
    assert bn["redundant_selects"] == 2 and bn["redundant_full_folder_reads"] == {}
    assert bn["most_frequent_command"] in ("SELECT", "UID MOVE")


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
    assert rep["fetches_per_folder"]["INBOX"] >= 2                # 同夾被重抓
    assert rep["redundant_full_folder_reads"]                     # 冗餘偵測點亮（守門有效）


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
