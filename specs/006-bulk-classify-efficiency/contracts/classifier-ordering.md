# Contract — Classifier: existence source & candidate ordering

## `build_report` — 存在性來源改用 `list_uids`

- 判斷「來源郵件是否存在」時，經 `ClassifyCache` 呼叫 `backend.list_uids(current_folder, on_progress=...)`（取代原 `list_headers` 萃取 uid）。
- 每來源夾在一次分類流程**最多查一次**（FR-002）；報告與執行共用同一 `ClassifyCache`。
- 逐列判定（SKIP / CANDIDATE / INFEASIBLE）與原因文字 **等價現況**（FR-003）。
- 檢查報告**列出順序 = 輸入工作表列序**（FR-006，不變）。

### 不變式
- 同一輸入工作表 → 報告逐列判定與優化前 100% 一致（SC-002），含「已標刪未清除郵件視為存在」。

## `execute` — 候選分組處理、輸出還原 CSV 列序

- 輸入 `items`；取 `candidates(items)`（CSV 列序）。
- **處理順序**：以 `(current_folder, target_folder)` 之**穩定排序**決定（同夾相鄰、決定性；FR-004、SC-004）。
- **回傳**：`list[MoveResult]` 依**原 CSV 列序**排列（FR-012、SC-008）。
- **結果不變式**（FR-005）：被搬移郵件集合與其目標夾、各列成功/失敗、dry-run 預設，與優化前 100% 一致（SC-005）。
- **進度**：`on_progress(done, total)` 仍逐候選推進至 `total`（不退化；FR-007）。
- **早停**：連續失敗達門檻提前停止之既有語意保留（於分組順序下判斷）；未處理者不列入回傳；cli「剩餘 N 筆」回報不變。
- `ReauthRequired` 仍向外傳（不當單列失敗），由 cli 乾淨停止 + 回報已完成/未完成（既有）。

## CLI 呈現

- 逐列成功/失敗清單依 `execute` 回傳順序輸出 → 即 **CSV 列序**（FR-012）。
- 匯出工作表 / 列出標題（功能1/2）不受影響（FR-008、SC-006）。

## 後端中立

- `classifier` / `cli` 僅透過 `MailBackend.list_uids` 參與，不 import imaplib、不特例化任一後端（Principle I、FR-009）。
