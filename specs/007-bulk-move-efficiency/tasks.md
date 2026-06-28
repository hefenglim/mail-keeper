---
description: "Task list for 007-bulk-move-efficiency (P4 grouping + P3 no-redundant-SELECT + P2 batch move + C1 fallback idempotency)"
---

# Tasks: 大量分類搬移的效能與冪等（Bulk Move Efficiency & Idempotency, Phase 2）

**Input**: Design documents from `specs/007-bulk-move-efficiency/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED — 憲法 Principle V（Test-First, NON-NEGOTIABLE）。每個行為任務先寫失敗測試（Red）再實作（Green）。跨 imaplib seam 一律走 **IMAP 模擬器引擎**（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`），雙層驗證（指令日誌 `redundant_selects`/`command_counts`/`bottleneck` + `snapshot()`、`arm_expiry` 注入異常）；分類層走 `FakeBackend`。

**Organization**: 依 user story（US1 P1 效能 / US2 P2 冪等·重連 / US3 P3 無回歸·安全）。完成後升 **0.6.2**。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔、無未完成相依）｜路徑：`src/mailkeeper/`、`tests/`

---

## Phase 1: Setup

- [ ] T001 確認基線：`$env:PYTHONPATH='src'; python -m pytest -q`（全綠、記錄數）+ `python -m mypy src/mailkeeper`（乾淨）；確認 `pyproject.toml` runtime 相依不變。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: US1/US2 共用的搬移路徑基礎（選取追蹤 + 批次上限常數）。先寫失敗測試。

- [ ] T002 [P] `src/mailkeeper/config.py` 新增 `MOVE_BATCH_MAX` 常數（程式內固定預設，如 200；不開放 config，P6 延後）。
- [ ] T003 失敗測試（Red）`tests/test_imap_loop_regression.py`：以真 client over 引擎，對同一夾連續多次 `move`/`mark_read` → `server.redundant_selects() == 0`、該夾 `SELECT` 計數 = 1；重連後（`arm_expiry`）會重新 SELECT（選取狀態已重置）。
- [ ] T004 最小實作（Green）`src/mailkeeper/imap_client.py`：加 `self._selected` 狀態 + `_ensure_selected(mailbox, readonly=False)`（僅未選/夾不同/模式不同才 `select`）；`connect()`/`_reconnect()` 重置 `self._selected=None`；`_move_impl`/`mark_read`/`flag` 改走 `_ensure_selected`。使 T003 轉綠。

**Checkpoint**: 免重複 SELECT 就位、重連正確重置；US1/US2 可開工。

---

## Phase 3: User Story 1 - 大量分類搬移大幅減少網路往返（P1）🎯 MVP

**Goal**: 候選分組 + 批次 UID MOVE，使來源夾 SELECT N→1、搬移往返 N→⌈N/批⌉；結果/輸出/安全等價現況。

**Independent Test**: 500 封同 (來源→目標) → `redundant_selects()==0`、`command_counts["UID MOVE"]`=⌈N/批⌉；交錯工作表分組決定性、結果 CSV 序等價（quickstart V1/V2）。

### Tests for User Story 1（先寫，必須先 FAIL）⚠️

- [ ] T005 [P] [US1] 失敗測試（Red）`tests/test_imap_loop_regression.py`：真 client over 引擎，500 封同 (來源→目標) `move_many` → `redundant_selects()==0`、`command_counts["UID MOVE"]==⌈500/MOVE_BATCH_MAX⌉`（非 500）、`snapshot()` 僅目標 uid 變動、他人 `\Deleted` 不被波及。
- [ ] T006 [P] [US1] 失敗測試（Red）`tests/test_imap_server_p2.py`：`move_many` 批次中某 uid 來源已不存在 → 退回逐封歸因：成功者搬、失敗者於回傳 dict 記錯、不連坐同批其他封（請求端 + snapshot 驗證）。
- [ ] T007 [P] [US1] 失敗測試（Red）`tests/test_backend.py`：`FakeBackend.move_many` 回 `dict[uid, None|error]`（全成功 / 部分失敗）。
- [ ] T008 [P] [US1] 失敗測試（Red）`tests/test_classifier.py`：`execute` 依 (current,target) 分組、處理順序同夾相鄰且決定性（兩次一致）；回傳 `MoveResult` 依 **CSV 列序**；`on_progress` 每批 `done += 該批封數`、達 total；結果集合與逐封等價。

### Implementation for User Story 1

- [ ] T009 [US1] `src/mailkeeper/organizer.py`：`MailBackend` 協定新增 `move_many(uids, dest_folder, mailbox="INBOX") -> dict[str, str | None]`（向後相容）；`tests/conftest.py` `FakeBackend.move_many`（in-memory，含部分失敗）。使 T007 轉綠。
- [ ] T010 [US1] `src/mailkeeper/imap_client.py`：`move_many` + `_move_many_impl`（`_ensure_selected` → `UID MOVE <set>` 分塊 `MOVE_BATCH_MAX`；批次非 OK/不支援 → 該塊退回逐封 `move` 歸因），`_with_reconnect` 包裝。使 T005/T006 轉綠。
- [ ] T011 [US1] `src/mailkeeper/classifier.py`：`execute` 依 (current,target) 穩定分組（保留原索引）、逐群分塊 `move_many`、結果映回並依 CSV 列序回傳；進度每批推進；**早停改連線層級**（移除連續資料失敗計數；單列失敗只記不早停、連線層級失敗往外傳）。使 T008 轉綠。
- [ ] T012 [US1] 遷移既有早停測試 `tests/test_classifier.py`：`test_execute_aborts_after_consecutive_failures` / `test_execute_threshold_configurable` 改為連線層級語意（單列資料失敗不早停；連線中斷且重連用盡 → 停），或以新語意重寫；全套回綠。

**Checkpoint**: US1 完成——同夾搬移免重選 + 批次、分組決定性、結果 CSV 序等價、進度不退化。

---

## Phase 4: User Story 2 - 搬移在中斷／重試下不產生重複複本（P2）

**Goal**: 後備搬移冪等（C1）+ 搬移中途重連續完，0 重複 / 0 遺漏。

**Independent Test**: 後備路徑 COPY 後中斷重試 → 目標複本正好 1（quickstart V4）；搬移中途重連 → 全完成不重複（V5）。

### Tests / 引擎前置（先寫，必須先 FAIL）⚠️

- [ ] T013 [P] [US2] 引擎擴充（§7：先加保真案例）`tests/imap_server.py` + `tests/imaplib_probe.py`：母版郵件帶 `Message-ID`；`_search_match` 支援 `HEADER Message-ID <id>`；於 `tests/test_imap_server.py`/`test_imap_server_behaviors.py` 加對拍真 imaplib 的保真測試。
- [ ] T014 [P] [US2] 失敗測試（Red）`tests/test_imap_server_p2.py`：伺服器不支援 `UID MOVE`（走後備），`arm_expiry` 於「COPY 後、標刪/EXPUNGE 前」注入中斷 + `token_provider` → 透明重連重試 → `snapshot()` 目標該封複本數 **==1**、來源正確移除、他人 `\Deleted` 不被波及。**同時移除 feature 006 的 C1 xfail marker**（`test_fallback_move_idempotency_across_copy_known_limitation` → 應 xpass/pass）。
- [ ] T015 [P] [US2] 失敗測試（Red）`tests/test_imap_server_p2.py`：`arm_expiry(before_op="move", nth=k, mode="eof")` 搬移中途中斷 → 重連後全部完成、0 重複 / 0 遺漏、`loop_report()["authentications"]>=2`。

### Implementation for User Story 2

- [ ] T016 [US2] `src/mailkeeper/imap_client.py`：`_move_impl` 後備路徑改冪等——uid 不在來源→成功 no-op；uid 在→取 `Message-ID`、在目標夾 `UID SEARCH HEADER Message-ID` 偵測既有複本：有則跳 COPY 只補標刪+`UID EXPUNGE`，無則 COPY→標刪→`UID EXPUNGE`；無 Message-ID → 盡力 COPY（docstring 標註殘留）。使 T014/T015 轉綠。

**Checkpoint**: US2 完成——後備搬移冪等、重連續完無重複；C1 修復、xfail 移除。

---

## Phase 5: User Story 3 - 結果與安全等價（無回歸）（P3）

**Goal**: 確認只改「如何送命令」，結果/輸出/安全全等價。

- [ ] T017 [P] [US3] 回歸與安全測試 `tests/test_imap_loop_regression.py` / `tests/test_classifier.py`：(1) 同工作表搬移結果集合、逐列成敗與順序（CSV 序）與優化前等價；(2) `snapshot()` 他人 `\Deleted` 全程不被波及（SC-007）；(3) 單列資料失敗不早停、其餘仍處理 vs 連線中斷且重連用盡才停（SC-010，分別注入驗證）；(4) 未加執行旗標維持 dry-run、輸出無 token。
- [ ] T018 [US3] 遷移其他受影響測試 `tests/test_imap_dataset.py`（效率斷言配合批次/免重選）；確認 `test_full_simulation_regression_loop` 等 UID MOVE 計數/選取斷言更新為批次語意。

**Checkpoint**: US1+US2+US3 全綠、離線。

---

## Phase 6: Polish & Cross-Cutting

- [ ] T019 [P] `python -m mypy src/mailkeeper` 乾淨；覆蓋率 `imap_client.py` ≥88%、總 ≥85%（`_ensure_selected`/`move_many`/後備冪等須被引擎測試覆蓋）。
- [ ] T020 執行 `quickstart.md` V1–V8，逐項對照 SC-001..SC-010 通過。
- [ ] T021 升版（Principle VI）：`pyproject.toml` + `src/mailkeeper/__init__.py` `0.6.1 → 0.6.2`（兩處一致）；`CHANGELOG.md` 加 `## [0.6.2] - <真實交付日期>`（P4 分組 + P3 免重選 + P2 批次 MOVE + C1 後備冪等；效能：500 同夾 SELECT N→1、MOVE N→⌈N/批⌉）。
- [ ] T022 [P] 文件同步：回填 `doc/mailkeeper-performance-report-20260627.html` 狀態表 **P2/P3/P4 → ✅ v0.6.2**、backlog **C1/C2 標示已修**（更新卡片、最後更新、修訂紀錄）；同步 `memory/roadmap-backlog.md`、`memory/perf-optimization-report.md`。

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2: T002–T004)** 阻擋 US1/US2。
- **US1 (P3 phase: T005–T012)** 依 Foundational；T009→T010（move_many 實作）；T011 依 T010；T012 依 T011。
- **US2 (T013–T016)** 依 Foundational + US1（後備走逐封 `move`，move_many 退路會用到）；T013（引擎 HEADER 搜尋）先於 T014；T016 依 T013/T014。
- **US3 (T017–T018)** 依 US1+US2。
- **Polish (T019–T022)** 最後。
- **Gate（CLAUDE.md §4，implement 前）**：`tasks.md` 後先跑 `/speckit.checklist` + `/speckit.analyze`，修正任何憲法違規才 `/speckit.implement`。

### Story 內 TDD 順序
- 測試（Red）先於實作（Green）：T003→T004；T005/T006/T007/T008→T009→T010→T011→T012；T013→T014/T015→T016。

### 平行機會
- T005/T006/T007/T008（US1 不同檔測試）可平行；T013 與 T014/T015 起草可平行；T019/T022 可平行。

---

## Implementation Strategy

- **MVP（US1）**：Setup + Foundational + US1 → 同夾免重選 + 批次 MOVE 落地（最大效能價值），quickstart V1/V2 驗證後可獨立交付。
- **增量**：US2（冪等·重連）→ US3（無回歸）→ Polish（升 0.6.2 + 文件同步）。

## Notes

- 跨 seam 一律走引擎、測請求端（嚴禁手刻 imaplib 回應，CLAUDE.md §7）。
- C1 穩健解＝目標夾 Message-ID 去重（先擴引擎 HEADER 搜尋）；僅靠來源 `\Deleted` 不足。
- 早停改連線層級：單列資料失敗不再早停（移除連續計數），連線層級失敗（重連用盡）才停。
- 完成務必 T022（文件同步），維持效能報告「已做/未做」一目了然。
