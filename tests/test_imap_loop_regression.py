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
    fresh_server,
)
from imap_sim import DELETED
from imap_transport import connected_client

from mailkeeper import classifier
from mailkeeper.csv_io import ClassificationRow


def _rows(*specs):
    return [ClassificationRow(*s) for s in specs]


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
