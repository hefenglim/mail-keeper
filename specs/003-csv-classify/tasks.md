# Tasks: 啟動選單與 CSV 郵件匯出／分類

**Input**: Design documents from `specs/003-csv-classify/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [data-model.md](./data-model.md), [contracts/interfaces.md](./contracts/interfaces.md), [research.md](./research.md)
**Tests**: REQUIRED — TDD per Constitution Principle V、CLAUDE.md §4 與 spec SC-007（離線測試）。每個行為任務 test-first（Red → Green → Refactor）。

**Organization**: 依使用者故事（US1/US2/US3）分組，可獨立實作與測試。

## Format: `[ID] [P?] [Story] Description`

- **[P]**：可平行（不同檔、無未完成相依）。路徑皆為 repo 相對路徑。

---

## Phase 1: Setup（共用基礎）

- [x] T001 擴充 `tests/conftest.py`：`FakeBackend` 新增 `list_folders()`、`list_headers(folder)`，並讓 `move(uid, dest_folder, mailbox="INBOX")` 記錄來源（`("move", uid, dest_folder, mailbox)`，欄名與真實簽章 `dest_folder`/`mailbox`＝來源資料夾 一致）；加入多資料夾 fixture（資料夾清單 + 各資料夾 headers，含特殊字元的主旨/寄件者）與寫/讀 CSV 的暫存輔助 — 在 `tests/conftest.py`

---

## Phase 2: Foundational（阻擋所有 US）

**⚠️ 完成前任何 US 不能開始。**

- [x] T002 [P] 失敗測試：(a) `MailHeader` 把 `recipients`（預設 `""`）**附加為最後一個欄位（置於 `date` 之後）**，且既有 4 位置引數建構仍正確對映到 uid/subject/sender/date；(b) IMAP 資料夾名稱解析器 `_parse_folder_name` 對樣本 LIST 回應（含引號、階層分隔、modified-UTF-7）正確 — 在 `tests/test_backend.py`
- [x] T003 在 `MailHeader` **最後**加 `recipients: str = ""`（置於 `date` 之後，勿插在前面）；在 `MailBackend` Protocol 新增 `list_folders() -> list[str]` 與 `list_headers(folder) -> list[MailHeader]` — 在 `src/mailkeeper/imap_client.py` 與 `src/mailkeeper/organizer.py`
- [x] T004 實作 `OutlookIMAPClient`：純函式 `_parse_folder_name(line)`、`list_folders()`（`IMAP4.list()` + 解析）、`list_headers(folder)`（select + fetch `HEADER.FIELDS (SUBJECT FROM TO DATE)`，沿用 `_decode`），`list_inbox_headers` 改為轉呼 `list_headers("INBOX")`（跨資料夾搬移與建立資料夾沿用既有 `move`／`ensure_folder`，無需新增） — 在 `src/mailkeeper/imap_client.py`

**Checkpoint**：後端能力與領域型別就緒，三個 US 可開始。

---

## Phase 3: User Story 1 — 選資料夾並匯出分類工作表（Priority: P1）🎯 MVP

**Goal**：選一個資料夾 → 匯出該資料夾郵件的「分類工作表」CSV（固定欄序、純標頭、含空白 `target_folder`）。
**Independent Test**：對多資料夾假信箱選某資料夾匯出，CSV 依序含 `uid, current_folder, target_folder, date, from, to, subject`、`target_folder` 空白、特殊字元正確跳脫。

### Tests（先寫、須失敗）

- [x] T005 [P] [US1] 失敗測試：`csv_io.write_worksheet(headers, folder, path)` 輸出固定欄序表頭與每列、`target_folder` 空白、含逗號/引號/換行的欄位正確跳脫；並以含中/英/日/韓/阿拉伯文與 emoji 的樣本斷言多語文欄位正確（對齊 SC-002 ≥5 語文）；UTF-8 — 在 `tests/test_csv_io.py`

### Implementation

- [x] T006 [US1] 實作 `csv_io.write_worksheet(headers, folder, path)`（stdlib `csv`，固定欄序 `uid,current_folder,target_folder,date,from,to,subject`） — 在 `src/mailkeeper/csv_io.py`
- [x] T007 [US1] 失敗測試：`cli` 的 `export-worksheet --folder <name> --out <path>` 子指令以 `backend.list_headers(folder)` + `csv_io.write_worksheet` 產出檔案（注入 `FakeBackend`） — 在 `tests/test_cli_csv_flow.py`
- [x] T008 [US1] 實作 `export-worksheet` 子指令（argparse 串接 backend 與 csv_io） — 在 `src/mailkeeper/cli.py`

**Checkpoint**：US1 可獨立運作（選資料夾→工作表 CSV）。

---

## Phase 4: User Story 2 — 匯出資料夾清單（Priority: P1）

**Goal**：列舉所有資料夾並輸出參考用 CSV（`folder` 欄；本期不含 `count`）。
**Independent Test**：對多資料夾假信箱匯出，CSV 列出所有資料夾名稱。

### Tests（先寫、須失敗）

- [x] T009 [P] [US2] 失敗測試：`csv_io.write_folders(folders, path)` 輸出表頭 `folder`（本期僅此欄）與每個資料夾、UTF-8、正確跳脫 — 在 `tests/test_csv_io.py`

### Implementation

- [x] T010 [US2] 實作 `csv_io.write_folders(folders, path)`（本期只輸出 `folder` 欄、不含 `count`） — 在 `src/mailkeeper/csv_io.py`
- [x] T011 [US2] 失敗測試：`cli` 的 `export-folders --out <path>` 子指令以 `backend.list_folders()` 產出 CSV — 在 `tests/test_cli_csv_flow.py`
- [x] T012 [US2] 實作 `export-folders` 子指令 — 在 `src/mailkeeper/cli.py`

**Checkpoint**：US1 + US2 皆可獨立運作。

---

## Phase 5: User Story 3 — 匯入、檢查報告、確認、搬移（Priority: P2）

**Goal**：讀工作表 → 比較 `target` vs `current` → 檢查報告（dry-run）→ 明確確認 → 只搬可行的變動列。
**Independent Test**：注入含「可行變動列／無變動列／不可行列」的 CSV，確認報告正確三分、確認前零 `move`、確認後僅可行候選被搬。

### Tests（先寫、須失敗）

- [x] T013 [P] [US3] 失敗測試：`csv_io.read_worksheet(path)` 依表頭解析、容忍多餘欄、缺 `uid`/`current_folder`/`target_folder` 任一或壞檔→乾淨錯誤 — 在 `tests/test_csv_io.py`
- [x] T014 [P] [US3] 失敗測試：`classifier.build_report(backend, rows)` 將列分為 skip／candidate／infeasible（驗證來源資料夾與 UID 存在、目標存在或可建立）；`classifier.execute(backend, candidates)` 僅對候選 `ensure_folder`+`move`、回報每列成功/失敗、未確認時零 `move`；**執行時來源 UID 已不存在（含被同次較早列搬走）者列為該列失敗、不崩潰** — 在 `tests/test_classifier.py`
- [x] T015 [P] [US3] 失敗測試：`cli` 的 `classify --in <path>` 預設只印報告（dry-run、零 `move`）；`--run` 後才搬移 — 在 `tests/test_cli_csv_flow.py`

### Implementation

- [x] T016 [US3] 實作 `csv_io.read_worksheet(path) -> list[ClassificationRow]`（含驗證與乾淨錯誤） — 在 `src/mailkeeper/csv_io.py`
- [x] T017 [US3] 實作 `classifier.build_report(...)` 與 `classifier.execute(...)`（只依賴 `MailBackend`；**目標資料夾不存在時預設自動建立 `ensure_folder`，僅當建立失敗才標為不可行**；`execute` 對「來源 UID 已不存在」逐列回報失敗、不崩潰） — 在 `src/mailkeeper/classifier.py`
- [x] T018 [US3] 實作 `classify` 子指令（預設報告；`--run` 或互動確認後執行；沿用 001 錯誤邊界） — 在 `src/mailkeeper/cli.py`

**Checkpoint**：三個 US 皆可獨立運作。

---

## Phase 6: 選單與非互動（跨 US）

**Goal**：啟動互動選單路由三功能；非互動安全（never-stuck）。依賴 US1–US3 的功能存在。

- [x] T019 [P] 失敗測試：`menu` 將選項 1/2/3/0 路由到對應功能（以替身驗證）；無子指令且無 TTY 時印用法 + 非零結束、不卡死；並斷言**提供子指令時於非 TTY 下執行至完成、不阻塞於 `input()`**（FR-002(a)） — 在 `tests/test_menu.py` 與 `tests/test_cli_csv_flow.py`
- [x] T020 實作 `menu.py`（互動迴圈，路由到三功能；功能3 先顯示報告再詢問確認） — 在 `src/mailkeeper/menu.py`
- [x] T021 串接 `cli.main`：argparse 子指令；無子指令且 TTY → 進 `menu`；無子指令且非 TTY → 用法 + 非零結束（沿用 `console.setup` 與錯誤邊界） — 在 `src/mailkeeper/cli.py`

**Checkpoint**：選單與三功能端到端可用（離線測試）。

---

## Phase 7: Polish & Cross-Cutting

- [x] T022 [P] 版本 `0.3.0` → `0.4.0`（`pyproject.toml` 與 `src/mailkeeper/__init__.py`）
- [x] T023 [P] `CHANGELOG.md` 加真實日期的 `0.4.0` 條目（選單 + 三 CSV 功能 + MailBackend 擴充）
- [x] T024 [P] 更新 `README.md`／`MailKeeper-Handoff.html` 說明選單與 `export-worksheet`/`export-folders`/`classify` 子指令
- [x] T025 執行 `mypy src/mailkeeper` 與 `pytest`（離線）至全綠；視需要更新 `src/mailkeeper/__init__.py` 對外匯出
- [x] T026 [P] 負向測試：工作表/資料夾 CSV 的輸出內容與所有錯誤訊息皆不含 token/機密字串（Principle IV） — 在 `tests/test_csv_io.py`
- [x] T027 [P] 失敗測試：**分別**斷言 `csv_io.write_worksheet` 與 `csv_io.write_folders` 對已存在的目標檔皆**覆寫**（兩者皆 FR-016） — 在 `tests/test_csv_io.py`
- [x] T028 [P] 失敗測試：匯出/匯入時路徑不可讀或不可寫 → 乾淨非零錯誤、不崩潰（FR-012／SC-006） — 在 `tests/test_cli_csv_flow.py`

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T004)** → 各 US。
- **US1 (T005–T008)** 與 **US2 (T009–T012)** 互不相依（皆 P1，可平行）。
- **US3 (T013–T018)** 需 Foundational；其 CSV 讀入沿用 US1 的 `csv_io`（同檔，序列）。
- **Phase 6 (T019–T021)** 需 US1–US3 的功能存在。
- **Polish (T022–T025)** 最後。

## Parallel Opportunities

- T002、T005、T009、T013、T014、T015、T019、T026、T027、T028 為各自獨立測試檔，可平行（[P]）。
- 觸及同一檔（`csv_io.py`／`cli.py`）的實作任務須序列。
- US1 與 US2 可由不同人平行推進。

## Implementation Strategy

MVP = US1（選資料夾→匯出工作表）。接著 US2（資料夾清單）、US3（檢查報告＋確認＋搬移），再加選單與非互動，最後 Polish。每個故事 test-first；先確認測試因正確原因失敗，再寫最小實作；每完成一任務或邏輯群組即提交。
