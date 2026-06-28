---
description: "Task list for 008-bulk-fetch-resilience (P5 resumable reconnect + P6 configurable FETCH batch + P7 lean header parse)"
---

# Tasks: 大量信箱讀取的韌性與調校（Bulk Fetch Resilience & Tuning, Phase 3）

**Input**: Design documents from `specs/008-bulk-fetch-resilience/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED — 憲法 Principle V（Test-First）。行為任務先寫失敗測試（Red）再實作（Green）。跨 imaplib seam 走 **IMAP 模擬器引擎**（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`）：`arm_expiry`/`set_uidvalidity` 注入、`loop_report`/`command_counts`/`assert_all_fetches_request_uid` + 最終結果不變量；config 走 `config_store` 單元測試；解析等價以母版逐字比對。引擎無需擴充。

**Organization**: US1（P5 續傳 P1，含 P7 解析同在 list_headers）/ US2（P6 批量可調 P2）/ US3（P7 等價 + 無回歸 P3）。完成後升 **0.6.3**。

## Format: `[ID] [P?] [Story] Description` ｜ [P]=可平行（不同檔）

---

## Phase 1: Setup
- [ ] T001 確認基線：`$env:PYTHONPATH='src'; python -m pytest -q`（全綠、記錄數）+ `python -m mypy src/mailkeeper`（乾淨）；確認 runtime 相依不變。

## Phase 2: Foundational
- [ ] T002 [P] `src/mailkeeper/config.py` 加 `FETCH_BATCH_DEFAULT = 50`（取代模組私有 `_FETCH_BATCH` 預設值，list_headers 改用實例批量）。
- [ ] T003 失敗測試（Red）`tests/test_config_store.py`：`fetch_batch_size` 解析——缺漏→50；`"abc"`/`0`/`-3`/`null`→50（不崩潰）；正整數 M→M。
- [ ] T004 最小實作（Green）`src/mailkeeper/config_store.py`：`Configuration` 加 `fetch_batch_size: int = config.FETCH_BATCH_DEFAULT`；`load()` 以 `_as_positive_int(data.get("fetch_batch_size"), config.FETCH_BATCH_DEFAULT)` 解析。使 T003 轉綠。

**Checkpoint**: 批量設定就位（供 US1/US2）。

## Phase 3: User Story 1 - 讀取重連可續傳（P5）🎯 MVP + P7 解析

**Goal**: `list_headers` 重連後從中斷處續抓、不整批重抓；UIDVALIDITY 變更安全重抓；進度延續；標頭解析改 header-only、輸出等價。

**Independent Test**: 中途斷線重連 → `UID FETCH` 次數≈⌈N/批⌉（不翻倍）、結果完整/UID 全非空/無重複遺漏（quickstart V1/V2/V5）。

### Tests for US1（先寫，必須先 FAIL）⚠️
- [ ] T005 [P] [US1] 失敗測試（Red）`tests/test_imap_loop_regression.py`：`bulk_server(n)`、真 client、`arm_expiry(before_op="fetch", nth=2, mode="eof")` + `token_provider` → `list_headers` 回 n 筆、UID 全非空、無重複/遺漏；`command_counts["UID FETCH"]` 未翻倍（≈⌈n/批⌉）、`redundant_full_folder_reads=={}`、`authentications>=2`、`assert_all_fetches_request_uid()`；on_progress 的 done 單調遞增不歸零、最終達 total。
- [ ] T006 [P] [US1] 失敗測試（Red）`tests/test_imap_server_p2.py`：讀取中途斷線且 `set_uidvalidity("INBOX", new)` → 重連後偵測 UIDVALIDITY 變更 → 整批重抓 → 結果正確（不沿用過時 UID、無錯亂、UID 全非空）。
- [ ] T007 [P] [US1/US3] 失敗測試（Red）`tests/test_backend.py` 或 `tests/test_imap_loop_regression.py`：對含 ASCII/CJK/emoji/encoded-word/折行主旨母版 `list_headers`，主旨/寄件者等（含解碼）逐字等價現況（P7 BytesHeaderParser）。

### Implementation for US1
- [ ] T008 [US1] `src/mailkeeper/imap_client.py`：`__init__` 接受 `fetch_batch_size`（預設 `config.FETCH_BATCH_DEFAULT`，存 `self._fetch_batch=max(1,…)`）；`list_headers` 改自帶**可續傳韌性迴圈**（`collected` 跨重連保留、re-`SEARCH ALL` 取差集只抓差集、`_current_uidvalidity()` 偵測變更→`collected.clear()`、進度 `len(collected)`、有界重連且成功批重置失敗計數）；讀標頭改 `email.parser.BytesHeaderParser`（P7）。使 T005/T006/T007 轉綠。
- [ ] T009 [US1] 遷移既有測試：`tests/test_imap_server_p2.py::test_list_headers_eof_mid_fetch_refetches_whole`（原斷言「整批重抓」）改為**續抓**語意；核對 `test_multibatch_fetch_over_100_messages_drives_progress`（批量 50 → 仍 3 批，預期不變）。全套回綠。

**Checkpoint**: US1 完成——續傳、UIDVALIDITY 重抓、解析等價、進度延續。

## Phase 4: User Story 2 - FETCH 批量可調（P6）
- [ ] T010 [P] [US2] 失敗測試（Red）`tests/test_imap_loop_regression.py`：`connected_client(..., fetch_batch_size=M)` 讀 N 封 → `command_counts["UID FETCH"]==⌈N/M⌉`（驗實例批量生效）。
- [ ] T011 [US2] `src/mailkeeper/cli.py`：建構 `OutlookIMAPClient` 時注入 `cfg.fetch_batch_size`；`tests/imap_transport.py::connected_client` 透傳 `fetch_batch_size`（測試支援）。使 T010 轉綠。

**Checkpoint**: 批量可由 config 調整、生效。

## Phase 5: User Story 3 - 解析等價與無回歸（P7）
- [ ] T012 [P] [US3] 回歸測試 `tests/test_cli_csv_flow.py` / 既有：匯出工作表／列標題輸出與優化前一致；分類路徑（006/007）測試全綠（無回歸）。

**Checkpoint**: US1+US2+US3 全綠、離線。

## Phase 6: Polish
- [ ] T013 [P] `mypy` 乾淨；覆蓋率 `imap_client.py` ≥88%、總 ≥85%（續傳迴圈/UIDVALIDITY/解析須被引擎測試覆蓋）。
- [ ] T014 執行 `quickstart.md` V1–V6，逐項對照 SC-001..SC-006。
- [ ] T015 升版（Principle VI）：`pyproject.toml` + `src/mailkeeper/__init__.py` `0.6.2 → 0.6.3`；`CHANGELOG.md` 加 `## [0.6.3] - <真實交付日期>`（P5 讀取重連可續傳、P6 fetch_batch_size 可調、P7 header-only 解析）。
- [ ] T016 [P] 文件同步：回填 `doc/mailkeeper-performance-report-20260627.html` 狀態表 **P5/P6/P7 → ✅ v0.6.3**；同步 `memory/roadmap-backlog.md`、`memory/perf-optimization-report.md`。

---

## Dependencies & Execution Order
- Setup(P1) → Foundational(P2: T002–T004) → US1(T005–T009) → US2(T010–T011) → US3(T012) → Polish(T013–T016)。
- TDD：T003→T004；T005/T006/T007→T008→T009；T010→T011。
- **Gate（implement 前）**：`/speckit.checklist` + `/speckit.analyze`，修正後才 `/speckit.implement`。
- 平行：T005/T006/T007 不同檔可平行；T013/T016 可平行。

## Notes
- 跨 seam 走引擎、測請求端（嚴禁手刻 imaplib 回應，CLAUDE.md §7）。引擎已具 `arm_expiry`/`set_uidvalidity`/log，無需擴充。
- 續傳以 UID 差集（穩健）；UIDVALIDITY 變更整批重抓；進度延續。
- 僅動唯讀 `list_headers`；分類/搬移（006/007）不碰。
