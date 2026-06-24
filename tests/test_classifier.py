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

    monkeypatch.setattr(backend, "list_headers", boom)
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
    # B：兩封同夾搬移，來源資料夾標頭只抓一次（非每封重抓 O(n×m)）
    rows = _rows(("10", "INBOX", "Work"), ("11", "INBOX", "Archive"))
    items = classifier.build_report(folder_backend, rows)
    calls: list[str] = []
    real = folder_backend.list_headers

    def spy(folder, *, on_progress=None):
        calls.append(folder)
        return real(folder, on_progress=on_progress)

    monkeypatch.setattr(folder_backend, "list_headers", spy)
    classifier.execute(folder_backend, items)
    assert calls.count("INBOX") == 1


def test_execute_aborts_after_consecutive_failures(make_backend, monkeypatch):
    # C：連續多筆失敗（疑似連線中斷）→ 提前停止，不對死連線狂試 25 次
    headers = [MailHeader(str(i), "S", "a@x", "d") for i in range(1, 6)]  # INBOX 5 封
    backend = make_backend(folders={"INBOX": headers, "Work": []})
    items = classifier.build_report(backend, _rows(*[(str(i), "INBOX", "Work") for i in range(1, 6)]))

    def boom(*a, **k):
        raise OSError("EOF occurred in violation of protocol")

    monkeypatch.setattr(backend, "move", boom)
    results = classifier.execute(backend, items)
    assert len(results) == 3 and all(not r.ok for r in results)  # 停在第 3 次連續失敗
    assert len(classifier.candidates(items)) == 5  # 仍有 2 筆未嘗試


def test_new_folders_lists_to_be_created(folder_backend):
    # E：列出候選中將被新建（target 不存在）的資料夾；已存在者（Work）不列
    rows = _rows(("10", "INBOX", "Work"), ("11", "INBOX", "NewA"), ("20", "Work", "NewB"))
    items = classifier.build_report(folder_backend, rows)
    assert classifier.new_folders(folder_backend, items) == ["NewA", "NewB"]


def test_build_report_wires_progress_to_source_reads(folder_backend, monkeypatch):
    # 體驗修正：功能3 初步檢驗（讀來源夾標頭）也要能接上進度回呼，避免大量郵件時像當機。
    import contextlib

    seen: dict[str, object] = {}
    real = folder_backend.list_headers

    def spy(folder, *, on_progress=None):
        seen[folder] = on_progress
        return real(folder, on_progress=on_progress)

    monkeypatch.setattr(folder_backend, "list_headers", spy)

    labels: list[str] = []

    def factory(label):
        labels.append(label)
        return contextlib.nullcontext(lambda d, t: None)

    classifier.build_report(folder_backend, _rows(("10", "INBOX", "Work")), progress=factory)
    assert seen.get("INBOX") is not None  # 來源夾讀取已接上進度回呼
    assert any("INBOX" in lbl for lbl in labels)  # 進度標籤帶資料夾名


def test_execute_reports_progress(folder_backend):
    rows = _rows(("10", "INBOX", "Work"), ("20", "Work", "Archive"))
    items = classifier.build_report(folder_backend, rows)
    seen: list[tuple[int, int]] = []
    classifier.execute(folder_backend, items, on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 2), (2, 2)]  # 2 候選 → 逐封回報 done/total


def test_execute_threshold_configurable(make_backend, monkeypatch):
    # US3：連續失敗門檻可由參數調整（cli 由 config 帶入）。預設 3，這裡設 2 → 停在第 2 次。
    headers = [MailHeader(str(i), "S", "a@x", "d") for i in range(1, 6)]
    backend = make_backend(folders={"INBOX": headers, "Work": []})
    items = classifier.build_report(backend, _rows(*[(str(i), "INBOX", "Work") for i in range(1, 6)]))
    monkeypatch.setattr(backend, "move", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    results = classifier.execute(backend, items, max_consecutive_failures=2)
    assert len(results) == 2  # 門檻=2 → 停在第 2 次連續失敗


def test_execute_stale_uid_reported_as_failure(folder_backend):
    # 同次兩列都搬 uid 10；第一列搬走後第二列來源已無 10 → 失敗、不崩潰
    rows = _rows(("10", "INBOX", "Work"), ("10", "INBOX", "Archive"))
    items = classifier.build_report(folder_backend, rows)
    results = classifier.execute(folder_backend, items)
    assert results[0].ok is True
    assert results[1].ok is False
    moves = [a for a in folder_backend.actions if a[0] == "move"]
    assert len(moves) == 1  # 第二列從未真正搬移
