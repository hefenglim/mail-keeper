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
    """來源夾現存 UID 集合：每夾只查一次並快取（顯示進度）。只取 UID、**不抓標頭內容**
    （P1：以 ``list_uids`` 取代整夾標頭 FETCH，大幅減往返/流量）。不吞例外——來源夾存在卻
    讀取失敗（連線中斷/逾時）應如實往外傳，而非把列誤標為不可行而遮蔽連線錯誤。"""
    if folder not in cache.source_uids:
        with make(f"檢查「{folder}」現存郵件") as cb:
            cache.source_uids[folder] = backend.list_uids(folder, on_progress=cb)
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
    max_consecutive_failures: int | None = None,  # deprecated/inert（feature 007）：不再驅動早停
) -> list[MoveResult]:
    """對可行候選**分組批次**搬移：依 (來源夾, 目標夾) 穩定分組、同群以 ``move_many`` 批次搬移；
    結果依**原 CSV 工作表列序**回傳（FR-002）。目標夾不存在則自動建立；來源 UID 執行時已不存在
    則該列回報失敗（不連坐同批其他封）。

    每處理完一個候選即回報進度 ``on_progress(done, total)``。來源夾現存 UID 取自共用 ``cache``
    （報告階段已讀即重用、不二次整夾掃描）。**早停改連線層級**：單列資料失敗只記為失敗列、繼續
    處理；``ReauthRequired`` 往外傳（cli 乾淨停止、由 ``on_progress`` 回報已完成數）；其他連線層級
    失敗（``move_many`` 因重連用盡而拋出）→ 停止並回傳已處理結果。``max_consecutive_failures``
    已停用（保留參數僅為向後相容）。
    """
    cache = cache if cache is not None else ClassifyCache()
    make = progress or _noop_reporter
    existing = _folders(backend, cache)
    cands = candidates(items)
    total = len(cands)
    results: dict[int, MoveResult] = {}
    done = 0

    def _advance() -> None:
        nonlocal done
        done += 1
        if on_progress is not None:
            on_progress(done, total)

    # 同一 (來源夾, uid) 只搬「CSV 首現者」；後續重複列視為失敗（等價現況：先到先搬，後者落空）
    claimed: dict[tuple[str, str], int] = {}
    dup: set[int] = set()
    for i in range(len(cands)):
        k = (cands[i].row.current_folder, cands[i].row.uid)
        if k in claimed:
            dup.add(i)
        else:
            claimed[k] = i
    # 勝出者依 (來源夾, 目標夾) 穩定分組（同群相鄰、決定性）
    winners = sorted(
        (i for i in range(len(cands)) if i not in dup),
        key=lambda i: (cands[i].row.current_folder, cands[i].row.target_folder),
    )
    pos = 0
    while pos < len(winners):
        key = (cands[winners[pos]].row.current_folder, cands[winners[pos]].row.target_folder)
        end = pos
        while end < len(winners) and (
            cands[winners[end]].row.current_folder,
            cands[winners[end]].row.target_folder,
        ) == key:
            end += 1
        group = winners[pos:end]
        src, tgt = key
        if tgt not in existing:
            backend.ensure_folder(tgt)
            existing.add(tgt)
        present = _source_uids(backend, src, cache, make)
        to_move = [cands[i].row.uid for i in group if cands[i].row.uid in present]
        try:
            outcome = backend.move_many(to_move, tgt, src) if to_move else {}
        except ReauthRequired:
            raise  # 需重新登入 → 終結；cli 乾淨停止（已完成數由 on_progress 回報）
        except Exception:
            break  # 連線層級失敗（move_many 內重連已用盡）→ 停止，回傳已處理
        for i in group:
            row = cands[i].row
            if row.uid not in present:
                results[i] = MoveResult(row, False, "來源 UID 在執行時已不存在")
            else:
                err = outcome.get(row.uid)
                if err is None:
                    present.discard(row.uid)  # 搬走即更新快取（不重抓整夾）
                    results[i] = MoveResult(row, True)
                else:
                    results[i] = MoveResult(row, False, err)
            _advance()
        pos = end
    # CSV 重複列（uid 已被前列認領）→ 失敗
    for i in sorted(dup):
        results[i] = MoveResult(cands[i].row, False, "來源 UID 在執行時已不存在")
        _advance()
    return [results[i] for i in range(len(cands)) if i in results]
