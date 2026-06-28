"""US3 — classification engine: report + execute. Test-first."""
from __future__ import annotations

import pytest

from mailkeeper import classifier
from mailkeeper.csv_io import ClassificationRow
from mailkeeper.imap_client import MailHeader


def _rows(*tuples) -> list[ClassificationRow]:
    return [ClassificationRow(*t) for t in tuples]


def test_build_report_classifies(folder_backend):
    rows = _rows(
        ("10", "INBOX", "Work"),        # candidate
        ("11", "INBOX", "INBOX"),       # skip (target == current)
        ("11", "INBOX", ""),            # skip (blank target)
        ("99", "INBOX", "Work"),        # infeasible: uid not in INBOX
        ("10", "Nope", "Work"),         # infeasible: source folder missing
        ("10", "INBOX", "NewFolder"),   # candidate (target auto-created)
    )
    items = classifier.build_report(folder_backend, rows)
    assert [it.status for it in items] == [
        "candidate", "skip", "skip", "infeasible", "infeasible", "candidate",
    ]


def test_rerun_already_moved_email_is_infeasible_not_duplicate(folder_backend):
    # 重跑同一檔：已搬走的郵件（uid 已不在來源）→ 不可行、不列候選 → 無重複搬移、不崩潰
    items = classifier.build_report(folder_backend, _rows(("999", "INBOX", "Work")))
    assert items[0].status == classifier.INFEASIBLE
    assert classifier.candidates(items) == []


def test_build_report_propagates_source_fetch_error(make_backend, monkeypatch):
    # F：來源夾存在但讀取失敗（連線中斷）→ 往外傳，而非把列誤標「不可行」遮蔽錯誤
    backend = make_backend(folders={"INBOX": [MailHeader("10", "S", "a@x", "d")]})

    def boom(folder, *, on_progress=None):
        raise OSError("connection lost")

    monkeypatch.setattr(backend, "list_uids", boom)
    with pytest.raises(OSError):
        classifier.build_report(backend, _rows(("10", "INBOX", "Work")))


def test_build_report_does_not_mutate(folder_backend):
    classifier.build_report(folder_backend, _rows(("10", "INBOX", "Work")))
    assert not any(a[0] == "move" for a in folder_backend.actions)


def test_execute_moves_only_candidates_and_autocreates(folder_backend):
    rows = _rows(("10", "INBOX", "Work"), ("11", "INBOX", ""), ("20", "Work", "NewFolder"))
    items = classifier.build_report(folder_backend, rows)
    results = classifier.execute(folder_backend, items)
    assert [r.ok for r in results] == [True, True]
    moves = [a for a in folder_backend.actions if a[0] == "move"]
    assert ("move", "10", "Work", "INBOX") in moves
    assert ("move", "20", "NewFolder", "Work") in moves
    assert ("folder", "NewFolder") in folder_backend.actions  # auto-created


def test_execute_fetches_source_once_per_folder(folder_backend, monkeypatch):
    # B：兩封同夾搬移，來源夾現存查詢只一次（非每封重查 O(n×m)）
    rows = _rows(("10", "INBOX", "Work"), ("11", "INBOX", "Archive"))
    items = classifier.build_report(folder_backend, rows)
    calls: list[str] = []
    real = folder_backend.list_uids

    def spy(folder, *, on_progress=None):
        calls.append(folder)
        return real(folder, on_progress=on_progress)

    monkeypatch.setattr(folder_backend, "list_uids", spy)
    classifier.execute(folder_backend, items)
    assert calls.count("INBOX") == 1


def test_execute_stops_on_connection_level_failure(make_backend, monkeypatch):
    # feature 007：早停改連線層級——move_many 因重連用盡而拋出 → execute 停止、回傳已處理
    headers = [MailHeader(str(i), "S", "a@x", "d") for i in range(1, 6)]  # INBOX 5 封
    backend = make_backend(folders={"INBOX": headers, "Work": []})
    items = classifier.build_report(backend, _rows(*[(str(i), "INBOX", "Work") for i in range(1, 6)]))

    def boom(*a, **k):
        raise OSError("EOF occurred in violation of protocol")

    monkeypatch.setattr(backend, "move_many", boom)
    results = classifier.execute(backend, items)
    assert results == []  # 第一群（同 INBOX→Work）即連線層級失敗 → 停止、無已處理
    assert len(classifier.candidates(items)) == 5  # 5 筆未完成（由 cli 回報剩餘）


def test_new_folders_lists_to_be_created(folder_backend):
    # E：列出候選中將被新建（target 不存在）的資料夾；已存在者（Work）不列
    rows = _rows(("10", "INBOX", "Work"), ("11", "INBOX", "NewA"), ("20", "Work", "NewB"))
    items = classifier.build_report(folder_backend, rows)
    assert classifier.new_folders(folder_backend, items) == ["NewA", "NewB"]


def test_execute_reports_progress(folder_backend):
    rows = _rows(("10", "INBOX", "Work"), ("20", "Work", "Archive"))
    items = classifier.build_report(folder_backend, rows)
    seen: list[tuple[int, int]] = []
    classifier.execute(folder_backend, items, on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 2), (2, 2)]  # 2 候選 → 逐封回報 done/total


def test_execute_max_consecutive_failures_is_inert(make_backend, monkeypatch):
    # feature 007：max_consecutive_failures 已停用（保留參數向後相容）；資料失敗不早停、不論其值
    headers = [MailHeader(str(i), "S", "a@x", "d") for i in range(1, 6)]
    backend = make_backend(folders={"INBOX": headers, "Work": []})
    items = classifier.build_report(backend, _rows(*[(str(i), "INBOX", "Work") for i in range(1, 6)]))
    # move_many 回每封資料層錯誤（非連線層級例外）→ 全部如實回報、不早停
    monkeypatch.setattr(backend, "move_many", lambda uids, d, m="INBOX": {u: "boom" for u in uids})
    results = classifier.execute(backend, items, max_consecutive_failures=1)
    assert len(results) == 5 and all(not r.ok for r in results)  # 全部處理（門檻無效）


def test_execute_stale_uid_reported_as_failure(folder_backend):
    # 同次兩列都搬 uid 10；第一列搬走後第二列來源已無 10 → 失敗、不崩潰
    rows = _rows(("10", "INBOX", "Work"), ("10", "INBOX", "Archive"))
    items = classifier.build_report(folder_backend, rows)
    results = classifier.execute(folder_backend, items)
    assert results[0].ok is True
    assert results[1].ok is False
    moves = [a for a in folder_backend.actions if a[0] == "move"]
    assert len(moves) == 1  # 第二列從未真正搬移


# ── feature 006 (P1)：存在性檢查改走 list_uids（不抓整夾標頭）─────────────────

def test_build_report_uses_list_uids_not_list_headers(folder_backend, monkeypatch):
    # P1/FR-001：報告階段以 list_uids 判存在性，完全不呼叫 list_headers（不抓標頭）
    uid_calls: list[str] = []
    hdr_calls: list[str] = []
    real_uids = folder_backend.list_uids

    def uid_spy(folder, *, on_progress=None):
        uid_calls.append(folder)
        return real_uids(folder, on_progress=on_progress)

    def hdr_spy(folder="INBOX", *, on_progress=None):
        hdr_calls.append(folder)
        return []

    monkeypatch.setattr(folder_backend, "list_uids", uid_spy)
    monkeypatch.setattr(folder_backend, "list_headers", hdr_spy)
    classifier.build_report(folder_backend, _rows(("10", "INBOX", "Work"), ("11", "INBOX", "Archive")))
    assert uid_calls.count("INBOX") == 1   # FR-002：每來源夾查一次
    assert hdr_calls == []                  # FR-001/SC-001：報告階段零標頭抓取


def test_build_report_preserves_worksheet_row_order(folder_backend):
    # FR-004：報告列出順序＝輸入工作表列序（本期不改順序）
    rows = _rows(("11", "INBOX", "Archive"), ("10", "INBOX", "Work"), ("99", "INBOX", "Work"))
    items = classifier.build_report(folder_backend, rows)
    assert [it.row.uid for it in items] == ["11", "10", "99"]


def test_execute_groups_by_source_then_target_csv_output(make_backend):
    # feature 007 (P4)：交錯來源夾 → 同夾相鄰處理；MoveResult 依原 CSV 列序
    backend = make_backend(folders={
        "INBOX": [MailHeader("1", "s", "a", "d"), MailHeader("3", "s", "a", "d")],
        "Promo": [MailHeader("2", "s", "a", "d"), MailHeader("4", "s", "a", "d")],
        "A": [], "B": [],
    })
    rows = _rows(("1", "INBOX", "A"), ("2", "Promo", "B"), ("3", "INBOX", "A"), ("4", "Promo", "B"))
    items = classifier.build_report(backend, rows)
    results = classifier.execute(backend, items)
    assert [r.row.uid for r in results] == ["1", "2", "3", "4"]   # 輸出依 CSV 列序
    assert all(r.ok for r in results)
    moved = [a[1] for a in backend.actions if a[0] == "move"]
    assert moved == ["1", "3", "2", "4"]                          # 處理依分組（INBOX 群先、Promo 群後）


def test_execute_data_failure_does_not_stop_remaining(make_backend, monkeypatch):
    # feature 007 (SC-010)：單列資料失敗不早停、其餘仍處理；逐封歸因
    backend = make_backend(folders={"INBOX": [MailHeader(str(i), "s", "a", "d") for i in (1, 2, 3)], "Work": []})
    items = classifier.build_report(backend, _rows(("1", "INBOX", "Work"), ("2", "INBOX", "Work"), ("3", "INBOX", "Work")))
    monkeypatch.setattr(
        backend, "move_many", lambda uids, d, m="INBOX": {u: ("boom" if u == "2" else None) for u in uids}
    )
    results = classifier.execute(backend, items)
    assert len(results) == 3                          # 全部處理
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error == "boom"


def test_build_report_threads_progress_to_list_uids(folder_backend, monkeypatch):
    # FR-005/SC-006：build_report 把 on_progress 透傳到 list_uids，取得 determinate 進度
    import contextlib

    seen: dict[str, object] = {}
    real = folder_backend.list_uids

    def spy(folder, *, on_progress=None):
        seen[folder] = on_progress
        return real(folder, on_progress=on_progress)

    monkeypatch.setattr(folder_backend, "list_uids", spy)
    labels: list[str] = []

    def factory(label):
        labels.append(label)
        return contextlib.nullcontext(lambda d, t: None)

    classifier.build_report(folder_backend, _rows(("10", "INBOX", "Work")), progress=factory)
    assert seen.get("INBOX") is not None          # 存在性查詢已接上進度回呼
    assert any("INBOX" in lbl for lbl in labels)  # 進度標籤帶資料夾名
