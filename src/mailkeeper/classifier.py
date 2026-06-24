"""功能3 分類引擎：比較 current vs target、產生檢查報告、確認後搬移。

只依賴 ``MailBackend`` 介面與 ``ClassificationRow``；不認識 imaplib。
預設只到報告（dry-run），由 cli 在明確確認後才呼叫 :func:`execute`。

R7：同一分類流程透過共用的 :class:`ClassifyCache`，使「資料夾清單」與「各來源夾整夾標頭」
各只讀一次（報告階段所讀即權威，執行階段重用、不二次掃描；FR-007）。``ReauthRequired``
（需重新登入）不被當作單列失敗，往外傳由 cli 乾淨停止並回報已完成/未完成數。
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Callable, ContextManager

from . import config
from .csv_io import ClassificationRow
from .imap_client import ReauthRequired
from .organizer import MailBackend
from .progress import ProgressCallback  # 後端中立、無 imaplib，可安全跨層共用

SKIP = "skip"
CANDIDATE = "candidate"
INFEASIBLE = "infeasible"

# 進度回報工廠：給定標籤回傳一個上下文管理器，進入後得到 (done,total) 回呼。
# 由 cli 注入 ``progress.reporter``；預設為 no-op（離線測試／非互動不顯示）。
ReporterFactory = Callable[[str], "ContextManager[ProgressCallback]"]


def _noop_progress(done: int, total: int) -> None:
    return None


def _noop_reporter(label: str) -> "ContextManager[ProgressCallback]":
    return contextlib.nullcontext(_noop_progress)


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


@dataclass
class ClassifyCache:
    """同一分類流程共用的快取：資料夾清單與各來源夾現存 UID 集合**各只讀一次**（FR-007）。

    cli 建立一個並貫穿 :func:`build_report` → :func:`new_folders` → :func:`execute`，
    使報告階段所讀成為權威，執行階段重用、不重抓整夾。
    """

    folders: set[str] | None = None
    source_uids: dict[str, set[str]] = field(default_factory=dict)


def _folders(backend: MailBackend, cache: ClassifyCache) -> set[str]:
    if cache.folders is None:
        cache.folders = set(backend.list_folders())
    return cache.folders


def _source_uids(
    backend: MailBackend, folder: str, cache: ClassifyCache, make: ReporterFactory
) -> set[str]:
    """來源夾現存 UID 集合：每夾只抓一次並快取（顯示進度）。不吞例外——來源夾存在卻
    讀取失敗（連線中斷/逾時）應如實往外傳，而非把列誤標為不可行而遮蔽連線錯誤。"""
    if folder not in cache.source_uids:
        with make(f"讀取「{folder}」標頭") as cb:
            cache.source_uids[folder] = {h.uid for h in backend.list_headers(folder, on_progress=cb)}
    return cache.source_uids[folder]


def build_report(
    backend: MailBackend,
    rows: list[ClassificationRow],
    *,
    cache: ClassifyCache | None = None,
    progress: ReporterFactory | None = None,
) -> list[ReportItem]:
    """逐列分類為 skip / candidate / infeasible，不變更任何郵件（dry-run）。

    讀取各來源夾標頭時透過 ``progress`` 工廠顯示進度；讀取結果寫入 ``cache`` 供執行階段重用。
    """
    cache = cache if cache is not None else ClassifyCache()
    make = progress or _noop_reporter
    folders = _folders(backend, cache)

    items: list[ReportItem] = []
    for row in rows:
        if not row.target_folder or row.target_folder == row.current_folder:
            items.append(ReportItem(row, SKIP))
        elif not row.uid or not row.current_folder:
            items.append(ReportItem(row, INFEASIBLE, "缺 uid 或 current_folder"))
        elif row.current_folder not in folders:
            items.append(ReportItem(row, INFEASIBLE, f"來源資料夾不存在：{row.current_folder}"))
        elif row.uid not in _source_uids(backend, row.current_folder, cache, make):
            items.append(
                ReportItem(row, INFEASIBLE, f"來源郵件不存在：{row.uid}@{row.current_folder}")
            )
        else:
            # 目標不存在時預設自動建立，故此處視為可行。
            items.append(ReportItem(row, CANDIDATE))
    return items


def candidates(items: list[ReportItem]) -> list[ReportItem]:
    return [it for it in items if it.status == CANDIDATE]


def new_folders(
    backend: MailBackend, items: list[ReportItem], *, cache: ClassifyCache | None = None
) -> list[str]:
    """候選中 target_folder 尚不存在、執行時將被「新建」的資料夾（去重排序）。

    供檢查報告在使用者確認前列出副作用（將新建哪些資料夾）。傳入 ``cache`` 則重用已讀的資料夾清單。
    """
    existing = _folders(backend, cache) if cache is not None else set(backend.list_folders())
    return sorted(
        {it.row.target_folder for it in candidates(items) if it.row.target_folder not in existing}
    )


def execute(
    backend: MailBackend,
    items: list[ReportItem],
    *,
    on_progress: Callable[[int, int], None] | None = None,
    progress: ReporterFactory | None = None,
    cache: ClassifyCache | None = None,
    max_consecutive_failures: int | None = None,
) -> list[MoveResult]:
    """對可行候選逐列搬移：目標不存在則自動建立；來源 UID 執行時已不存在則回報失敗。

    每處理完一個候選（成功或失敗）即回報進度 ``on_progress(done, total)``。
    來源夾現存 UID 取自共用 ``cache``（報告階段已讀即重用、不二次整夾掃描；FR-007）。
    ``ReauthRequired`` 不當作單列失敗——往外傳，由 cli 乾淨停止並回報已完成/未完成（FR-004）。
    連續失敗達 ``max_consecutive_failures``（預設取自設定）→ 提前停止，不對死連線狂試。
    """
    cache = cache if cache is not None else ClassifyCache()
    make = progress or _noop_reporter
    limit = (
        max_consecutive_failures
        if max_consecutive_failures is not None
        else config.MAX_CONSECUTIVE_FAILURES
    )
    existing = _folders(backend, cache)
    cands = candidates(items)
    total = len(cands)
    results: list[MoveResult] = []

    consecutive_failures = 0
    for done, it in enumerate(cands, 1):
        row = it.row
        ok = False
        try:
            if row.target_folder not in existing:
                backend.ensure_folder(row.target_folder)
                existing.add(row.target_folder)
            present = _source_uids(backend, row.current_folder, cache, make)
            if row.uid not in present:
                results.append(MoveResult(row, False, "來源 UID 在執行時已不存在"))
            else:
                backend.move(row.uid, row.target_folder, row.current_folder)
                present.discard(row.uid)  # 搬走即更新快取（不重抓整夾）
                results.append(MoveResult(row, True))
                ok = True
        except ReauthRequired:
            raise  # 需重新登入 → 終結整體；由 cli 乾淨停止 + 回報已完成/未完成
        except Exception as exc:  # 單列失敗不影響其他列、不崩潰
            results.append(MoveResult(row, False, str(exc)))
        if on_progress is not None:
            on_progress(done, total)
        consecutive_failures = 0 if ok else consecutive_failures + 1
        if consecutive_failures >= limit:
            break  # 疑似連線中斷：提前停止；未處理者不在 results 中，由 cli 提示重試
    return results
