# Phase 0 Research: 輸出體驗優化

所有 spec 的不確定點已於 `/speckit.clarify`（2026-06-23）解決；本檔記錄各決策的選擇、理由與替代方案。

## D1 — CSV 編碼：UTF-8 + BOM（utf-8-sig）

- **Decision**: `csv_io` 寫入與讀取統一使用 `encoding="utf-8-sig"`。常數 `CSV_ENCODING = "utf-8-sig"`。
- **Rationale**: Microsoft Excel 在沒有 BOM 的 UTF-8 CSV 上會以系統地區編碼（如 cp950/cp1252）誤判，導致中文亂碼；加上 BOM 後 Excel 直接正確判讀。寫入用 `utf-8-sig` 會在檔首寫入 BOM；讀取用 `utf-8-sig` 會在有 BOM 時剝除、無 BOM 時等同 utf-8，因此能容忍「Excel 另存可能加/留 BOM」的往返（FR-002）。純文字編輯器與 Python/多數 CSV/LLM 解析器皆容忍 BOM。
- **Alternatives considered**:
  - 純 UTF-8（無 BOM）：檔案最乾淨、最利於嚴格解析，但 Excel 雙擊仍亂碼（需手動匯入精靈）—— 與使用者實際情境相違。
  - UTF-16-LE + Tab 分隔：Excel 原生最佳，但非標準 CSV、對其他工具/AI 不通用、檔案較大。
  - 程式內手動寫 BOM bytes：與 `utf-8-sig` 等效但易錯，無益。

## D2 — 大量標頭讀取：分批 fetch

- **Decision**: `imap_client.list_headers` 由「單次 `UID FETCH 全部`」改為「**分批** fetch（每批 N 封，預設 50）」，每解析完一封即回報進度。新增純函式 `_chunked(seq, size)` 供切批與單元測試。
- **Rationale**: 網路下載是 426 封情境的真正瓶頸；單次 fetch 是一個不可中斷的阻塞呼叫，無法在下載期間更新進度。分批後可在每批/每封後回報 `on_progress(done, total)`，使進度於下載期間即時前進（FR-016、SC-003），並讓記憶體用量有界。
- **Alternatives considered**:
  - 維持單次 fetch、僅在「下載後本地解析」階段顯示進度：最慢的網路段仍無回饋，未解決問題（被 clarify 否決）。
  - 以 IMAP `IDLE`/部分抓取等進階機制：過度複雜，超出本次範圍。
- **Trade-off**: 多了數次往返（426/50 ≈ 9 批），可忽略；換取真實進度。

## D3 — 進度如何跨層：後端中立 (done,total) 回呼

- **Decision**: 在會跑大迴圈的下層方法新增**後端中立**的可選參數 `on_progress: Callable[[int, int], None] | None = None`（`list_headers`、`classifier.execute`）。下層只「呼叫回呼」，不認識任何 UI；`progress.py` 提供 `reporter(label)` context manager，`yield` 出一個符合該回呼簽章的函式；UI 連線只在 `cli` 完成。
- **Rationale**: 維持 Principle I（後端隔離）與分層 —— `imap_client`/`classifier` 不 import UI 模組、不耦合顯示；`MailBackend` 協定僅以「新增可選參數」擴充，向後相容（既有呼叫端不傳即無進度）。回呼可在離線測試中以 spy 驗證被以 `(done,total)` 呼叫。
- **Alternatives considered**:
  - 在 `imap_client`/`classifier` 直接 import `progress` 並渲染：耦合後端/引擎到 UI，違反分層精神、難離線測試。
  - 讓 `list_headers` 改成 generator 逐封 yield、由 cli 迴圈驅動進度：會破壞既有「回傳 `list[MailHeader]`」契約與所有呼叫端，成本過高。

## D4 — 檔名補副檔名規則：`ensure_csv_suffix`

- **Decision**: 純函式 `ensure_csv_suffix(name) -> str`：以 `os.path.splitext` 取副檔名；若有非空且非單純 `.` 的副檔名則原樣返回；否則去除結尾的點後補 `.csv`。
- **Rationale**: 符合「只有在沒有副檔名時才補」（FR-004/005）。`splitext` 具路徑感知（目錄部分的點不算副檔名），自然處理 `out.dir/inbox` → `out.dir/inbox.csv`。
- **邊界**：
  - `inbox` → `inbox.csv`；`inbox.csv` → 不變；`data.txt` → 不變（尊重既有副檔名）。
  - `report.`（結尾點）→ `report.csv`；`a.b.c` → 不變（已有 `.c`）。
  - `out/inbox` → `out/inbox.csv`；空字串/純預設值由 cli 端的預設值處理，不進入本函式。
- **Alternatives considered**:
  - 強制一律改成 `.csv`（覆寫使用者打的 `.txt`）：違反「沒有附檔名才補」的明確需求。
  - 正則自製判斷：不如 `splitext` 穩健（路徑感知、跨平台）。
