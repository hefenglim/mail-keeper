# Phase 1 — Data Model: Bulk Classify Efficiency (Phase 1: Lean Existence Check)

本期不引入新的持久化資料；以下為跨層流動的領域概念與其約束。

## 既有型別（不變）

- **`MailHeader`**（`imap_client`）：`uid, subject, sender, date, recipients`。功能1/2 仍透過 `list_headers` 取得（含內容）。本期**不改**其結構與既有取得路徑。
- **`ClassificationRow`**（`csv_io`）：工作表一列（含 `uid, current_folder, target_folder` 等）。輸入不變。
- **`ReportItem`**（`classifier`）：`row, status(SKIP/CANDIDATE/INFEASIBLE), reason`。判定與原因**等價現況**（FR-003）；列出順序維持 CSV 序（FR-004）。
- **`MoveResult`**（`classifier`）：`row, ok, error`。本期**不改**——搬移路徑與輸出順序維持現況（FR-006）。
- **`ClassifyCache`**（`classifier`）：`folders: set[str] | None`、`source_uids: dict[str, set[str]]`。**重用**——`source_uids` 的填入來源由「`list_headers` 萃取 uid」改為「`list_uids` 直接回傳」；快取「每夾一次」精神不變（FR-002）。

## 新概念

### Source Folder Presence Set（來源夾現存集合）
- **型別**: `set[str]`（UID 字串集合）。
- **語意**: 某來源資料夾「目前現存」的郵件 UID 集合——涵蓋信箱中尚未 expunge 的所有郵件，**含已標 `\Deleted` 未清除者**（與現況 `UID SEARCH ALL` 一致；Clarify Q1）。
- **取得**: 後端 `list_uids(folder)` 單次最小化查詢；不含郵件內容。
- **快取**: 存於 `ClassifyCache.source_uids[folder]`，一次分類流程每夾最多查一次（FR-002）；執行階段 `present.discard(uid)` 維持鮮活（既有行為）。
- **驗證規則**: 元素為非空 UID 字串；集合可為空（空夾）。

## 狀態流（分類一次流程）

```text
read_worksheet → rows
build_report(rows, cache):
    folders = list_folders()                       # 既有，每流程一次
    for row in rows (CSV 序):                        # 報告列出順序 = CSV 序（FR-004）
        判定 skip / infeasible / candidate
        其中「來源存在?」→ list_uids(current) 經 cache（每夾一次；FR-002, P1，取代整夾 header FETCH）
execute(items, cache):                              # 本期不改：維持現況處理與輸出
    重用 cache.source_uids（存在性已快取，不二次查詢）
    逐候選搬移、回傳 MoveResult（順序同現況）
```

> 本期僅「存在性來源」由 `list_headers`→`list_uids`（P1）；`execute`／搬移與輸出順序不動。候選分組（P4）延至 P2/P3。
