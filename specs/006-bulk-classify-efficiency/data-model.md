# Phase 1 — Data Model: Bulk Classify Efficiency (Phase 1)

本期不引入新的持久化資料；以下為跨層流動的領域概念與其約束。

## 既有型別（不變）

- **`MailHeader`**（`imap_client`）：`uid, subject, sender, date, recipients`。功能1/2 仍透過 `list_headers` 取得（含內容）。本期**不改**其結構與既有取得路徑。
- **`ClassificationRow`**（`csv_io`）：工作表一列（含 `uid, current_folder, target_folder` 等）。輸入不變。
- **`ReportItem`**（`classifier`）：`row, status(SKIP/CANDIDATE/INFEASIBLE), reason`。判定與原因**等價現況**（FR-003）。
- **`MoveResult`**（`classifier`）：`row, ok, error`。集合等價現況（FR-005）；**呈現順序**改為 CSV 列序（FR-012）。
- **`ClassifyCache`**（`classifier`）：`folders: set[str] | None`、`source_uids: dict[str, set[str]]`。**重用**——`source_uids` 的填入來源由「`list_headers` 萃取 uid」改為「`list_uids` 直接回傳」；快取「每夾一次」精神不變（FR-002）。

## 新概念

### Source Folder Presence Set（來源夾現存集合）
- **型別**: `set[str]`（UID 字串集合）。
- **語意**: 某來源資料夾「目前現存」的郵件 UID 集合——涵蓋信箱中尚未 expunge 的所有郵件，**含已標 `\Deleted` 未清除者**（與現況 `UID SEARCH ALL` 一致；Clarify Q1）。
- **取得**: 後端 `list_uids(folder)` 單次最小化查詢；不含郵件內容。
- **快取**: 存於 `ClassifyCache.source_uids[folder]`，一次分類流程每夾最多查一次（FR-002）；執行階段 `present.discard(uid)` 維持鮮活（既有行為）。
- **驗證規則**: 元素為非空 UID 字串；集合可為空（空夾）。

### Classification Candidate Ordering（候選處理排序）
- **處理鍵**: `(current_folder, target_folder)` 之穩定排序 → 決定**內部處理順序**（同夾相鄰、決定性；FR-004、SC-004）。
- **原索引**: 保留各候選於 `candidates(items)`（即 CSV 列序）中的索引，供輸出還原。
- **輸出順序**: `execute` 回傳的 `list[MoveResult]` 依**原索引（CSV 列序）**排列（FR-012、SC-008）。
- **不變式**: 排序 MUST NOT 改變被搬移郵件集合、各列成功/失敗、dry-run 預設（FR-005）。

## 狀態流（分類一次流程）

```text
read_worksheet → rows
build_report(rows, cache):
    folders        = list_folders()              # 既有，每流程一次
    for row in rows (CSV 序):                      # 報告列出順序 = CSV 序（FR-006）
        判定 skip / infeasible / candidate
        其中「來源存在?」→ list_uids(current) 經 cache（每夾一次；FR-002, P1）
execute(items, cache):
    cands          = candidates(items)            # CSV 序
    order          = stable_sort(cands, key=(current,target))   # 內部處理序（P4）
    for i in order: 處理（重用 cache.source_uids）；記錄 result[原索引]
    return [result[i] for i in CSV 序]            # 輸出還原 CSV 序（FR-012）
```
