# Quickstart — Validation Guide: Bulk Fetch Resilience & Tuning (Phase 3)

全程**離線**：跨 seam 走 IMAP 模擬器引擎（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`，真 `OutlookIMAPClient`）。細節見 [data-model.md](./data-model.md) 與 [contracts/](./contracts/)。

## Prerequisites
```bash
pip install -e ".[test]"
```

## 核心驗證情境（對應 Success Criteria）

### V1 — 重連後續抓、不整批重抓（SC-001）｜US1
- **Given**：`bulk_server(n)`、`fetch_batch_size` 使 ≥2 批；`arm_expiry(before_op="fetch", nth=2, mode="eof")` + `token_provider`。
- **When**：`client.list_headers("INBOX", on_progress=...)`。
- **Then**：`command_counts["UID FETCH"]` ≈ ⌈n/批⌉（不翻倍）、`redundant_full_folder_reads=={}`；回 n 筆、UID 全非空、無重複/遺漏；`assert_all_fetches_request_uid()`；`authentications>=2`。

### V2 — UIDVALIDITY 變更安全重抓（SC-002）｜US1
- **Given**：讀到一半（注入斷線）期間 `set_uidvalidity("INBOX", new)`。
- **When**：重連後續讀。
- **Then**：偵測變更 → 整批重抓 → 結果正確（不沿用過時 UID、無錯亂）。

### V3 — FETCH 批量可調（SC-003）｜US2
- **Given(a)**：`config_store.load` 無 `fetch_batch_size` → ==50；值 `"abc"`/`0`/`-3`/`null` → ==50（不崩潰）；值 M → ==M。
- **Given(b)**：引擎讀 N 封、`OutlookIMAPClient(fetch_batch_size=M)`。**Then**：`command_counts["UID FETCH"]==⌈N/M⌉`。

### V4 — 標頭解析等價（SC-004 / SC-005）｜US3
- **When**：對含 ASCII/CJK/emoji/encoded-word/折行主旨的母版 `list_headers`。
- **Then**：每封 UID/主旨/寄件者/收件者/日期（含解碼）與優化前逐字一致；匯出/列標題輸出 100% 等價；分類路徑（006/007）無回歸。

### V5 — 進度跨重連延續（SC-006）｜US1
- **Given**：記錄 `(done,total)` 的 on_progress + 中途斷線。
- **Then**：`done` 單調遞增、不歸零、最終達 total；全程有回饋。

### V6 — 多次斷線 / 重連用盡｜US1（Edge）
- 多次 `arm_expiry`（persist 或多 nth）→ 每次續抓、最終完成；超過 `max_reconnect_attempts` → 如實外拋（不靜默產出不完整）。

## 執行
```bash
pytest -q
mypy src/mailkeeper
pytest --cov=mailkeeper --cov-report=term-missing   # imap_client ≥88%
```

## 交付後（追蹤）
- 回填 `doc/mailkeeper-performance-report-20260627.html`：P5/P6/P7 → ✅ v0.6.3。
- 同步 `CHANGELOG.md`（升版 0.6.3）與 `memory/roadmap-backlog.md`、`memory/perf-optimization-report.md`。
