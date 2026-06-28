---
description: "Task list for 006-bulk-classify-efficiency (Phase 1: Lean Existence Check)"
---

# Tasks: 大量信箱分類效能 — 第一期（Bulk Classify Efficiency, Phase 1: Lean Existence Check）

**Input**: Design documents from `specs/006-bulk-classify-efficiency/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED — 憲法 Principle V（Test-First, NON-NEGOTIABLE）。每個行為任務都先寫失敗測試（Red），再寫最小實作（Green）。跨 imaplib seam 一律走 **IMAP 模擬器引擎**（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`），測請求端 + log 驗往返/下載；分類層走 `FakeBackend`。

**Organization**: 依 user story 分組（US1 P1 存在性最小化 / US2 P2 無回歸）。本期**不含 P4 分組**（延至 P2/P3）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔、無未完成相依）
- **[Story]**: 所屬 user story（US1/US2）
- 路徑為單一專案：`src/mailkeeper/`、`tests/`

---

## Phase 1: Setup

**Purpose**: 確認起點、鎖定「不新增 runtime 相依、不動搬移路徑」。

- [ ] T001 確認基線：於分支 `006-bulk-classify-efficiency` 跑 `$env:PYTHONPATH='src'; python -m pytest -q`（**全綠，記錄實際測試數**）+ `python -m mypy src/mailkeeper`（乾淨）；確認 `pyproject.toml` runtime 相依不變。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 後端中立的「現存 UID 集合」能力（seam）——US1 的共用前提。先寫失敗測試。

- [ ] T002 [P] 失敗測試（Red）`tests/test_backend.py`：以 `OutlookIMAPClient` over `ImapServer` 驗 `list_uids("INBOX")` 契約——`command_counts` 出現 `UID SEARCH`、**無**該夾整夾完整標頭 `UID FETCH`；回傳集合**含**母版已標 `\Deleted` 的 UID（Clarify Q1）；`server.arm_expiry(before_op="search", nth=1, mode="eof")` + `token_provider` 下重連後仍回正確集合、不重複/不遺漏（FR-009）；同夾 `list_uids` 的 `bytes_*` 遠低於 `list_headers`（SC-003 佐證）。（依賴既有引擎；契約見 `contracts/backend-list-uids.md`）
- [ ] T003 最小實作（Green）`src/mailkeeper/imap_client.py`：新增 `list_uids(folder="INBOX", *, on_progress=None) -> set[str]` 與 `_list_uids_impl`（`select(folder, readonly=True)` + `uid("search", None, "ALL")` → 解析為 `set[str]`，含已標 `\Deleted`）；以 `self._with_reconnect(...)` 包裝（有界重試、唯讀冪等）；取得集合後以「該夾郵件數」為總數驅動 `on_progress` 之 determinate 進度至完成（不注入人工延遲）。使 T002 轉綠。（依賴 T002）
- [ ] T004 [P] 後端中立宣告 + 假後端（Green）：`src/mailkeeper/organizer.py` 的 `MailBackend` 協定新增 `list_uids(folder="INBOX", *, on_progress=None) -> set[str]`（向後相容）；`tests/conftest.py` 的 `FakeBackend` 實作 `list_uids`（回 in-memory UID 集合、驅動 `on_progress`）；於 `tests/test_backend.py` 補 `FakeBackend.list_uids` 進度與集合正確性小測。

**Checkpoint**: `list_uids` 在真後端（over 引擎）與假後端皆就位、行為一致；US1 可開工。

---

## Phase 3: User Story 1 - 大型信箱檢查報告快又省流量（Priority: P1）🎯 MVP

**Goal**: 分類「檢查報告」的存在性判斷改用 `list_uids`，消除整夾完整標頭抓取；判定與搬移結果等價現況。

**Independent Test**: 對大型來源夾跑 `build_report` → 報告逐列判定不變、該夾整夾 header FETCH=0、下載量大降（quickstart V1–V3）。

### Tests for User Story 1（先寫，必須先 FAIL）⚠️

- [ ] T005 [P] [US1] 失敗測試（Red）`tests/test_classifier.py`：以 `FakeBackend` 驗 `build_report` 經 `_source_uids` 走 `list_uids`（非 `list_headers`，以呼叫紀錄/spy 斷言）；逐列判定（skip/candidate/infeasible＋原因）與現況一致，**含引用已標 `\Deleted` UID 的列為 candidate**（Clarify Q1）；同一來源夾在一次流程的存在性查詢只一次（FR-002，cache 重用）；報告**列出順序＝輸入工作表列序**（FR-004）；`build_report` 透傳 `on_progress` 至 `list_uids`、得 determinate 進度（done→total）（FR-005／SC-006）。
- [ ] T006 [P] [US1] 失敗測試（Red）`tests/test_imap_loop_regression.py`：`build_report` over `bulk_server(n)`（真 client over 引擎）→ `loop_report()["fetches_per_folder"]` 該來源夾整夾完整標頭 FETCH = **0**、`command_counts` 出現 `UID SEARCH`；報告階段 `bytes_*` 較「現況 `list_headers` 取 uid」同情景下降 **≥90%**（SC-001/SC-003）；`assert_all_fetches_request_uid()` 仍成立。

### Implementation for User Story 1

- [ ] T007 [US1] 最小實作（Green）`src/mailkeeper/classifier.py`：`_source_uids` 改呼叫 `backend.list_uids(folder, on_progress=cb)`（取代 `{h.uid for h in backend.list_headers(...)}`）；進度標籤由「讀取『{folder}』標頭」改為存在性語意（如「檢查『{folder}』現存郵件」）。`execute`／搬移路徑**不動**。使 T005/T006 轉綠。（依賴 T003、T004）
- [ ] T008 [US1] 測試遷移 `tests/test_classifier.py`（及其他相關）：把既有假設 `_source_uids`→`list_headers` 的測試改判 `list_uids`，全套回綠；確認無殘留對「整夾標頭抓取」的舊期待。

**Checkpoint**: US1 完成——大型信箱檢查報告不再整夾抓標頭、判定等價、可獨立驗證。

---

## Phase 4: User Story 2 - 內容功能與搬移路徑無回歸（Priority: P2）

**Goal**: 確認本期只動「分類存在性來源」，其餘一切（功能1/2、搬移執行、安全）零回歸。

**Independent Test**: 匯出/列標題輸出逐字不變；同工作表搬移結果/順序/進度不變；dry-run 預設、無 secret（quickstart V6）。

- [ ] T009 [P] [US2] 回歸測試 `tests/test_cli_csv_flow.py` / `tests/test_imap_loop_regression.py`：(1) 匯出工作表 / 列出標題輸出（含 CJK/emoji/encoded-word 解碼）與優化前逐字一致（SC-005，仍走 `list_headers`）；(2) 同一工作表 `execute` 的搬移結果集合、逐列成功/失敗與順序、進度回報與優化前一致（SC-004，搬移路徑未改）；(3) 分類未加執行旗標仍 dry-run、輸出/錯誤無 token。

**Checkpoint**: US1 + US2 皆獨立成立，全套離線測試綠。

---

## Phase 5: Polish & Cross-Cutting

**Purpose**: 品質閘門、版本、文件同步。

- [ ] T010 [P] `python -m mypy src/mailkeeper` 乾淨；`python -m pytest --cov=mailkeeper --cov-report=term-missing` 達標（`imap_client.py` ≥88%、總 ≥85%）——`list_uids`/`_list_uids_impl` 須被引擎測試覆蓋。
- [ ] T011 執行 `quickstart.md` 的 V1–V6 驗證情境，逐項對照 SC-001..SC-006 通過。
- [ ] T012 升版（Principle VI）：`pyproject.toml` 與 `src/mailkeeper/__init__.py` 版本 `0.6.0 → 0.7.0`（兩處一致）；`CHANGELOG.md` 加 `## [0.7.0] - <真實交付日期>` 條目（分類存在性檢查最小化、新增後端中立 `list_uids`、效能：10k 來源夾報告往返/下載大降）。
- [ ] T013 [P] 文件同步追蹤：回填 `doc/mailkeeper-performance-report-20260627.html` 狀態表 **P1 → ✅ 已完成 v0.7.0**（更新對應卡片、頂端「最後更新」、底部修訂紀錄；**P4 維持未實作、標延至 P2/P3**）；同步 `memory/roadmap-backlog.md` 與 `memory/perf-optimization-report.md`。

---

## Dependencies & Execution Order

### Phase 依賴
- **Setup (P1)**：無依賴，先跑。
- **Foundational (P2)**：依 Setup；**阻擋** US1。
- **US1 (P3)**：依 Foundational（T003/T004）。
- **US2 (P4)**：依 US1（驗證 US1 改動後無回歸）；其回歸基準亦可獨立執行。
- **Polish (P5)**：依所有 user story 完成。

### Gate（CLAUDE.md §4，implement 前）
- 本 `tasks.md` 完成後，先跑 **`/speckit.checklist` + `/speckit.analyze`**（唯讀一致性/覆蓋檢查、修正任何憲法違規）**才** `/speckit.implement`。

### Story 內順序（TDD）
- 測試（Red）先於實作（Green）：T002→T003；T005/T006→T007→T008。
- Foundational 完成才動 US1 實作。

### 平行機會
- T002 與（規格已定的）T004 可平行起草；T005、T006 可平行（不同檔）；T009、T010、T013 可平行。

---

## Parallel Example: User Story 1

```text
# 先平行寫 US1 的失敗測試（不同檔）：
Task T005: classifier 單元測試（FakeBackend）— _source_uids 走 list_uids、判定等價、每夾一次
Task T006: loop-regression（引擎）— 整夾 header FETCH=0、UID SEARCH 出現、bytes 降 ≥90%
# 兩者 FAIL 後，再做 T007 實作使其轉綠。
```

---

## Implementation Strategy

### MVP（僅 US1）
1. Phase 1 Setup → 2. Phase 2 Foundational（`list_uids` seam）→ 3. Phase 3 US1 → **STOP & VALIDATE**（quickstart V1–V3：報告不再整夾抓標頭、判定等價、下載大降）。

### 增量交付
1. Setup + Foundational → seam 就緒。
2. US1 → 獨立驗證（MVP：最大效能價值）。
3. US2 → 回歸驗證（功能1/2 與搬移零回歸）。
4. Polish → 升版 + 文件同步 + 閘門。

---

## Notes

- [P] = 不同檔、無未完成相依。
- 跨 seam 一律走模擬器引擎（測請求端 + log），嚴禁手刻 imaplib 回應（CLAUDE.md §7）。
- 每任務或邏輯群組後提交；驗證測試先 FAIL 再實作。
- 本期**不動** `execute`／搬移路徑、**不做** P4 分組（延至 P2/P3）。
- 完成後務必執行 T013（文件同步），維持效能報告「已做/未做」一目了然。
