"""功能3 分類引擎：比較 current vs target、產生檢查報告、確認後搬移。

只依賴 ``MailBackend`` 介面與 ``ClassificationRow``；不認識 imaplib。
預設只到報告（dry-run），由 cli 在明確確認後才呼叫 :func:`execute`。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .csv_io import ClassificationRow
from .organizer import MailBackend

SKIP = "skip"
CANDIDATE = "candidate"
INFEASIBLE = "infeasible"


@dataclass(frozen=True)
class ReportItem:
    row: ClassificationRow
    status: str  # SKIP / CANDIDATE / INFEASIBLE
    reason: str = ""


@dataclass(frozen=True)
class MoveResult:
    row: ClassificationRow
    ok: bool
    error: str = ""


def build_report(backend: MailBackend, rows: list[ClassificationRow]) -> list[ReportItem]:
    """逐列分類為 skip / candidate / infeasible，不變更任何郵件（dry-run）。"""
    folders = set(backend.list_folders())
    uid_cache: dict[str, set[str]] = {}

    def uids_in(folder: str) -> set[str]:
        if folder not in uid_cache:
            # 不吞例外：來源夾「存在卻讀取失敗」（連線中斷/逾時）應如實往外傳，
            # 而非把所有列誤標為「不可行」而遮蔽真正的連線錯誤。資料夾不存在的
            # 情況已在呼叫前以 row.current_folder not in folders 處理。
            uid_cache[folder] = {h.uid for h in backend.list_headers(folder)}
        return uid_cache[folder]

    items: list[ReportItem] = []
    for row in rows:
        if not row.target_folder or row.target_folder == row.current_folder:
            items.append(ReportItem(row, SKIP))
        elif not row.uid or not row.current_folder:
            items.append(ReportItem(row, INFEASIBLE, "缺 uid 或 current_folder"))
        elif row.current_folder not in folders:
            items.append(ReportItem(row, INFEASIBLE, f"來源資料夾不存在：{row.current_folder}"))
        elif row.uid not in uids_in(row.current_folder):
            items.append(
                ReportItem(row, INFEASIBLE, f"來源郵件不存在：{row.uid}@{row.current_folder}")
            )
        else:
            # 目標不存在時預設自動建立，故此處視為可行。
            items.append(ReportItem(row, CANDIDATE))
    return items


def candidates(items: list[ReportItem]) -> list[ReportItem]:
    return [it for it in items if it.status == CANDIDATE]


# 連續多筆搬移失敗 → 疑似連線中斷（token 過期/EOF）→ 提前停止，不對死連線狂試。
_MAX_CONSECUTIVE_FAILURES = 3


def new_folders(backend: MailBackend, items: list[ReportItem]) -> list[str]:
    """候選中 target_folder 尚不存在、執行時將被「新建」的資料夾（去重排序）。

    供檢查報告在使用者確認前列出副作用（將新建哪些資料夾）。
    """
    existing = set(backend.list_folders())
    return sorted(
        {it.row.target_folder for it in candidates(items) if it.row.target_folder not in existing}
    )


def execute(
    backend: MailBackend,
    items: list[ReportItem],
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[MoveResult]:
    """對可行候選逐列搬移：目標不存在則自動建立；來源 UID 執行時已不存在則回報失敗。

    每處理完一個候選（成功或失敗）即回報進度 ``on_progress(done, total)``。
    """
    existing = set(backend.list_folders())
    cands = candidates(items)
    total = len(cands)
    results: list[MoveResult] = []

    # 每個來源資料夾的現存 UID 集合「只抓一次」並快取，搬走後即時更新。
    # 避免每搬一封都重抓整夾（O(n×m) 的冗餘並發；見 doc/lessons-learned.md）。
    present_cache: dict[str, set[str]] = {}

    def present_in(folder: str) -> set[str]:
        if folder not in present_cache:
            present_cache[folder] = {h.uid for h in backend.list_headers(folder)}
        return present_cache[folder]

    consecutive_failures = 0
    for done, it in enumerate(cands, 1):
        row = it.row
        ok = False
        try:
            if row.target_folder not in existing:
                backend.ensure_folder(row.target_folder)
                existing.add(row.target_folder)
            if row.uid not in present_in(row.current_folder):
                results.append(MoveResult(row, False, "來源 UID 在執行時已不存在"))
            else:
                backend.move(row.uid, row.target_folder, row.current_folder)
                present_in(row.current_folder).discard(row.uid)
                results.append(MoveResult(row, True))
                ok = True
        except Exception as exc:  # 單列失敗不影響其他列、不崩潰
            results.append(MoveResult(row, False, str(exc)))
        if on_progress is not None:
            on_progress(done, total)
        consecutive_failures = 0 if ok else consecutive_failures + 1
        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            break  # 疑似連線中斷：提前停止；未處理者不在 results 中，由 cli 提示重試
    return results
