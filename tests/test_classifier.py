"""US3 — classification engine: report + execute. Test-first."""
from __future__ import annotations

from mailkeeper import classifier
from mailkeeper.csv_io import ClassificationRow


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


def test_execute_stale_uid_reported_as_failure(folder_backend):
    # 同次兩列都搬 uid 10；第一列搬走後第二列來源已無 10 → 失敗、不崩潰
    rows = _rows(("10", "INBOX", "Work"), ("10", "INBOX", "Archive"))
    items = classifier.build_report(folder_backend, rows)
    results = classifier.execute(folder_backend, items)
    assert results[0].ok is True
    assert results[1].ok is False
    moves = [a for a in folder_backend.actions if a[0] == "move"]
    assert len(moves) == 1  # 第二列從未真正搬移
