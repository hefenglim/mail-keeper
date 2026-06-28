# Contract — Classifier: existence source via `list_uids`

## `build_report` — 存在性來源改用 `list_uids`

- 判斷「來源郵件是否存在」時，經 `ClassifyCache` 呼叫 `backend.list_uids(current_folder, on_progress=...)`（取代原 `list_headers` 萃取 uid）。
- 每來源夾在一次分類流程**最多查一次**（FR-002）；報告與執行共用同一 `ClassifyCache`（執行階段重用、不二次查詢）。
- 逐列判定（SKIP / CANDIDATE / INFEASIBLE）與原因文字 **等價現況**（FR-003）。
- 檢查報告**列出順序 = 輸入工作表列序**（FR-004，不變）。
- 進度：存在性查詢透過 `on_progress` 呈現 determinate 進度（總數=該夾郵件數，推進至完成；FR-005）。

### 不變式
- 同一輸入工作表 → 報告逐列判定與優化前 100% 一致（SC-002），含「已標刪未清除郵件視為存在」（Clarify Q1）。

## `execute` / 搬移路徑 — **本期不改**

- `execute` 重用 `ClassifyCache.source_uids`（存在性已於報告階段快取），不發出新的整夾查詢。
- 搬移處理順序、`MoveResult` 回傳順序、連續失敗早停、`ReauthRequired` 乾淨停止、dry-run 預設——**全部維持現況**（FR-006、SC-004）。
- 候選分組（P4）延至 P2/P3，本契約不涵蓋。

## CLI 呈現

- 逐列成功/失敗清單、匯出工作表、列出標題（功能1/2）——**不受影響**（FR-006、SC-005）。

## 後端中立

- `classifier` / `cli` 僅透過 `MailBackend.list_uids` 參與，不 import imaplib、不特例化任一後端（Principle I、FR-007）。
