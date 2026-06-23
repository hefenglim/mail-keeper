# Phase 1 Data Model: 輸出體驗優化

非傳統資料實體，而是本功能引入/調整的型別、常數與函式契約。

## 型別與常數

- **`ProgressCallback`** = `Callable[[int, int], None]`：以 `(done, total)` 呼叫，回報「已處理數／總數」。後端中立，不含任何 UI/協定細節。
- **`CSV_ENCODING`** = `"utf-8-sig"`（`csv_io` 模組常數）：所有 CSV 讀寫共用。

## `progress.py`（新模組，後端中立）

- **`reporter(label, *, stream=None, threshold=30)`** → context manager，`yield` 一個 `ProgressCallback`。
  - `stream`：輸出串流，預設 `sys.stderr`（經 `console` 包裝後為編碼安全）。
  - `threshold`：僅當 `total > threshold`（預設 30）時才顯示。
  - 行為：
    - 啟用條件（首次收到 `(done,total)` 時判定，並快取）：`stream.isatty()` 為真 **且** `total > threshold`。
    - 未啟用（非 TTY，或 `total ≤ 30`，或 `total` 未知且尚未超過門檻）→ 所有更新為 no-op、不輸出（FR-010、FR-015）。
    - 啟用時：以 `\r{label} {done}/{total} ({pct}%)` 就地更新，並做時間節流（例如 ≥0.1s 或 `done==total` 才重繪），避免洗版。
    - 離開 context（正常或例外）→ 若曾輸出則補一個換行收尾（FR-012）；例外照常往外傳遞（不吞錯）。
  - 不對資料輸出（stdout/CSV 檔）造成污染（FR-011）。
- **驗證/不可崩潰**：`total` 可為 `None`（未知）→ 視為「尚未達門檻前不顯示」；`done` 超過 `total` 時夾住為 `total`。任何輸出例外被吞掉（進度永不使主流程崩潰）。

## `csv_io.py`（調整）

- **`ensure_csv_suffix(name: str) -> str`**（新，純函式）：
  - 規則見 research D4。`splitext` 取副檔名；有非空且非 `.` 之副檔名 → 原樣；否則 `name.rstrip(".") + ".csv"`。
- **`write_worksheet` / `write_folders`**：開檔編碼改 `CSV_ENCODING`（utf-8-sig，寫 BOM）。其餘（固定欄序、跳脫、覆寫）不變。
- **`read_worksheet`**：讀檔編碼改 `CSV_ENCODING`（剝除 BOM、容忍無 BOM）。必要欄判定與容忍多餘欄不變。
- **相容**：欄位結構、必要欄、回傳 `ClassificationRow` 皆不變（FR-003）。

## `MailBackend`（`organizer.py`）與 `imap_client.py`（調整）

- **`MailBackend.list_headers(folder="INBOX", *, on_progress: ProgressCallback | None = None) -> list[MailHeader]`**：新增**可選 keyword-only** 參數；預設 `None` → 行為與現況一致（向後相容）。
- **`imap_client.OutlookIMAPClient.list_headers`**：實作改為以 `_chunked(uids, 50)` 分批 `UID FETCH`，每解析完一封即（若有）呼叫 `on_progress(done, total)`，`total = len(uids)`。
- **`_chunked(seq, size) -> Iterator[list]`**（新，純函式）：等分切批，供離線單元測試。
- **`FakeBackend.list_headers`**（測試）：接受並（可）呼叫 `on_progress`，以離線驗證回報。

## `classifier.py`（調整）

- **`execute(backend, items, *, on_progress: ProgressCallback | None = None) -> list[MoveResult]`**：對候選逐封搬移，每封後（若有）呼叫 `on_progress(done, total)`，`total = len(candidates)`。其餘（自動建夾、來源 UID 失效回報、單列失敗隔離）不變。
- **`build_report`**：不變（其耗時集中於 `list_headers`，已被進度涵蓋）。

## `cli.py`（調整）

- 互動提示與子指令路徑（`export-worksheet --out`、`export-folders --out`、`classify --in`、選單三項）取得使用者輸入後，先套 `ensure_csv_suffix`，再傳給 `csv_io`，並於確認訊息顯示**補完後**的實際檔名（FR-006）。
- 以 `progress.reporter(label)` 包住 `list_headers`（匯出/分類報告的標頭讀取）與 `classifier.execute`（搬移），把 `yield` 出的回呼以 `on_progress=` 傳入。
- 互動性沿用既有 `sys.stdin/stdout.isatty()`；進度串流為 `sys.stderr`（經 `console` 包裝）。
