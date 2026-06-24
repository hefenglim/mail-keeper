---
description: "Task list for 005-bulk-resilience"
---

# Tasks: 大量信箱的效能與韌性（Bulk Resilience, R7）

**Input**: Design documents from `specs/005-bulk-resilience/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED — 憲法 Principle V（Test-First, NON-NEGOTIABLE）。每個行為任務都先寫失敗測試（Red），再寫最小實作（Green）。全程離線，以 `tests/imap_sim.py::FakeIMAPConn` 母版模擬器，對拍真 imaplib，雙層驗證（指令日誌 + `snapshot()`）。

**Organization**: 依 user story 分組（US1 P1 / US2 P2 / US3 P3），各自獨立可測、可交付。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行（不同檔、無未完成相依）
- **[Story]**: 所屬 user story（US1/US2/US3）
- 路徑為單一專案：`src/mailkeeper/`、`tests/`

---

## Phase 1: Setup

**Purpose**: 確認起點，鎖定「不新增 runtime 相依」。

- [x] T001 確認基線：於分支 `005-bulk-resilience` 跑 `$env:PYTHONPATH='src'; python -m pytest -q`（**全綠，記錄實際數**）+ `python -m mypy src/mailkeeper`（乾淨）；確認本期不新增 `pyproject.toml` 的 runtime 相依（退避用 stdlib `time`）。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 所有 user story 共用的測試基礎設施與 seam 接線；**必須先完成**。每項先寫失敗測試。

- [x] T002 [P] 擴充模擬器失敗注入：在 `tests/imap_sim.py::FakeIMAPConn` 加可設定的失敗注入（指定第 N 次 `fetch`/`move` 擲 `AccessTokenExpired`/EOF 類錯誤，之後可恢復），並在指令日誌記錄重連相關呼叫；先於 `tests/test_imap_sim.py` 寫鎖定此行為的自我測試（Red→Green）。
- [x] T003 [P] 模擬器追蹤 token 提供者：`FakeIMAPConn`/`install()` 記錄注入的 token provider 被呼叫次數與回傳的 token（供驗證重連時有續期）；在 `tests/test_imap_sim.py` 補測試。
- [x] T004 [P] 新增 `ReauthRequired` 後端中立錯誤於 `src/mailkeeper/imap_client.py`（公開匯出、訊息不含 secret）；先在 `tests/test_imap_contract.py` 寫「被拋出時型別/訊息無 secret」失敗測試。
- [x] T005 [P] `src/mailkeeper/auth.py` 新增「僅靜默」續期路徑：回傳新 token 或擲 `ReauthRequired`，**絕不**退化為互動 device flow；先在 `tests/test_*`（離線、假 MSAL app）寫失敗測試（成功回 token、無快取/refresh 失效擲 `ReauthRequired`）。
- [x] T006 [P] 韌性設定：`src/mailkeeper/config.py` 加安全預設常數（`max_consecutive_failures`/`max_reconnect_attempts`/`max_retries_per_op`/`backoff_base_seconds`／**`backoff_cap_seconds`（退避封頂上限，具體值，讓「上限封頂」可測）**），`src/mailkeeper/config_store.py` 解析 `config.json` 可選鍵（無效→預設、永不崩潰）；先在 `tests/test_config_store.py` 寫失敗測試（缺漏用預設、無效退預設、有效生效）。
- [x] T007 `OutlookIMAPClient` 接線（`src/mailkeeper/imap_client.py`）：`__init__` 接受可選 `token_provider: Callable[[],str]` 與 `on_status: Callable[[str],None]`；`connect()` 取 token 時優先用 `token_provider`（無則沿用既有靜態 token，向後相容）；先在 `tests/test_imap_contract.py` 寫「connect 使用 provider 回傳的 token 組 XOAUTH2」失敗測試（依賴 T002/T003 模擬器）。

**Checkpoint**: 模擬器能演出 token 過期/EOF/重連、能追蹤 provider；`ReauthRequired`/靜默續期/韌性設定/`client` 接線就位。US1–US3 可開工。

---

## Phase 3: User Story 1 — 中途過期/斷線自動恢復並完成（Priority: P1）🎯 MVP

**Goal**: token 過期或連線中斷時，後端透明續期＋重連＋重試，分類項目級續做、匯出整批重抓；續期不可行則乾淨停止並回報。

**Independent Test**: token 中途過期情境下跑分類 `--run`，零人工介入 → 全部候選搬完、0 重複、0 遺漏（雙層驗證）。

- [x] T008 [P] [US1] 失敗測試（契約／指令日誌）：操作遇 `AccessTokenExpired` → 觸發靜默續期 + 重建連線 + 重新認證 + 重選夾 + 重試，日誌呈現有界重連序列。`tests/test_imap_contract.py`。
- [x] T009 [US1] 實作 `_with_reconnect(op)` 於 `src/mailkeeper/imap_client.py`：偵測 session 失效/EOF（research R4 標記）→ 呼叫 `token_provider` 續期 → 重建 `IMAP4_SSL` + XOAUTH2 重認證 → 必要時重 `select` → **有界退避重試**；`ReauthRequired` 直接外拋不重試。以之包住 `list_headers` 批次 fetch、`move`、`list_folders`。（依賴 T007）
- [x] T010 [P] [US1] 失敗測試（資料集／雙層）：token 中途過期 → 分類最終 100% 完成、0 重複、0 遺漏、母版他人 `\Deleted`(uid 106) 未波及。`tests/test_imap_dataset.py`。
- [x] T011 [US1] `cli._connect` 注入 `token_provider`（呼叫 `auth` 僅靜默續期）與 `on_status`（→ `console` 編碼安全 stderr）於 `src/mailkeeper/cli.py`；確認分類透明續做（依賴 T009）。
- [x] T012 [P] [US1] 失敗測試（cli）：`ReauthRequired` → 乾淨停止、印需重新登入訊息 + 已完成/未完成數量、非零退出、無 traceback、無 secret。`tests/test_cli_csv_flow.py` 或 `tests/test_cli_boundary.py`。
- [x] T013 [US1] 實作 cli 對 `ReauthRequired` 的乾淨停止與「已完成/未完成」回報於 `src/mailkeeper/cli.py`（classify 流程 + 錯誤邊界）。
- [x] T014 [P] [US1] 失敗測試（匯出整批重抓）：`list_headers` 下載中途斷線 → 重連後整批重抓 → 輸出完整、每列 UID 非空。`tests/test_imap_dataset.py` 或 `tests/test_imap_contract.py`。
- [x] T015 [US1] 匯出整批重抓：確認/調整 `list_headers` 在重連後從頭重跑 SEARCH+FETCH 於 `src/mailkeeper/imap_client.py`。**驗收**：標頭下載中途斷線 → 重連後整批重抓 → 輸出完整、每列 UID 非空（即 T014 所測）；唯讀、重跑安全。
- [x] T029 [P] [US1] dry-run 不被繞過（FR-011、Principle III 非協商）：失敗測試——重連/續做過程中，分類維持 dry-run 預設（未加 `--run`/未確認則不搬）、刪除範圍不擴大（重連後仍只處理確認過的候選，move 維持 `UID MOVE` + 安全 fallback、不波及他人 `\Deleted`）。`tests/test_cli_csv_flow.py` 或 `tests/test_imap_dataset.py`。

**Checkpoint**: US1 可獨立交付為 MVP —— 大量搬移在 token 中途過期下仍可靠完成或乾淨停止。

---

## Phase 4: User Story 2 — 同一流程整夾只讀一次（Priority: P2）

**Goal**: 一次分類流程對任一來源夾整夾標頭最多讀一次（報告權威快取、執行重用）。

**Independent Test**: 跑一次完整分類，量測來源夾整夾讀取 = 1 次。

- [x] T016 [P] [US2] 失敗測試：一次 classify 流程對來源夾整夾標頭（select+search+fetch 一輪）只出現一次；報告階段仍能標不存在郵件。`tests/test_classifier.py`（用模擬器指令日誌計次）。
- [x] T017 [US2] 實作：`classifier.build_report` 對外提供其權威 `uid_cache`；`classifier.execute` 接受 `present`（報告快取）為起始集合、不二次整夾掃描、搬走即 `discard`；`cli.classify` 把快取傳入；`list_folders` 一次分類流程內只取一次共用。檔案：`src/mailkeeper/classifier.py`、`src/mailkeeper/cli.py`。
- [x] T018 [P] [US2] 失敗測試（TOCTOU）：報告後到執行前某封被他處搬走 → execute（以快取為準、不重讀）讓 `move` 安全失敗回報、不重搬、不崩潰（冪等）。`tests/test_classifier.py`。

**Checkpoint**: 整夾讀取 2→1，正確性與冪等不變。

---

## Phase 5: User Story 3 — 退避重試 + 韌性門檻可設定（Priority: P3）

**Goal**: 暫時性抖動以有界退避重試吸收；韌性門檻可由設定調整。

**Independent Test**: 注入「短暫失敗即恢復」→ 操作完成；改設定 → 行為改變。

- [x] T019 [P] [US3] 失敗測試：某筆 move 短暫失敗一次後恢復 → 該筆最終成功、不因單次抖動觸發整體放棄。`tests/test_imap_contract.py` 或 `tests/test_imap_dataset.py`。
- [x] T020 [US3] 實作有界**指數退避**（stdlib `time.sleep`，以 `backoff_cap_seconds` 封頂）於 `src/mailkeeper/imap_client.py` 的 `_with_reconnect`/op 重試；次數、退避基準與封頂上限讀自韌性設定（封頂值具體、可測）。
- [x] T021 [P] [US3] 失敗測試：`classifier.execute` 的連續失敗門檻改讀設定；改 `config.json` 的 `max_consecutive_failures` → 行為改變；無效設定用安全預設。`tests/test_classifier.py` + `tests/test_config_store.py`。
- [x] T022 [US3] 接線韌性設定（`max_consecutive_failures`/`max_reconnect_attempts`/`max_retries_per_op`/`backoff_base_seconds`）由 config 流入 `classifier.execute` 與 `imap_client` 重連；移除 `classifier._MAX_CONSECUTIVE_FAILURES` 寫死。檔案：`src/mailkeeper/classifier.py`、`src/mailkeeper/cli.py`。

**Checkpoint**: 抖動可吸收，門檻可調，預設安全。

---

## Phase 6: Polish & Cross-Cutting

- [x] T028 進度狀態條依迴圈性質啟動（FR-013；可提前獨立完成）：先寫失敗測試（`tests/test_progress.py`）——`reporter(label, network=True)` 在件數 ≤ 30（如 3）且互動 TTY 下**仍顯示**；`network=False`（純 CPU）維持 **> 30 才顯示**；非 TTY 皆零輸出。再於 `src/mailkeeper/progress.py` 加 `network` 旗標（`network=True`→門檻 0、否則 30），並把所有**網路 in/out 迴圈**呼叫點改為 `network=True`：`cli.export_worksheet` 的標頭讀取、`cli.classify` 的搬移、`classifier.build_report`/`execute` 的來源夾讀取（經 `progress` 工廠）。檔案：`src/mailkeeper/progress.py`、`src/mailkeeper/cli.py`、`src/mailkeeper/classifier.py`。（與 US1/US2 的 cli/classifier 改動有交集，未標 [P]，合併時留意。）
- [x] T023 [P] 保真度：若重連引入任何新的 IMAP 回應結構，先於 `tests/test_imap_fidelity.py` 加對拍真 imaplib 的 fidelity case（`authenticate`/`select` 既有已涵蓋；確認無新增未驗證結構）。
- [x] T024 [P] 可見性（FR-009）+ 機密安全（FR-012）：端到端確認續期/重連/重試期間有 `on_status` 狀態輸出；**斷言所有 `on_status` 字串永不含 token/secret**（FR-012、Principle IV）；非 TTY 無 `\r`/控制字元污染。`tests/test_cli_csv_flow.py`。
- [x] T025 升版 + CHANGELOG：`pyproject.toml` 與 `src/mailkeeper/__init__.py` 版本一致升，`CHANGELOG.md` 新增條目（**真實交付日期**）。
- [x] T026 [P] 更新 `doc/release-smoke.md`：新增「讓操作橫跨 token 壽命 / 手動使 token 失效 → 驗證自動恢復或乾淨停止」的真實帳號檢查項。
- [x] T027 收尾驗證：`python -m pytest`（全綠、離線）+ `python -m mypy src/mailkeeper`（乾淨）+ `pwsh scripts/coverage.ps1`（套件 ≥85%、`imap_client` ≥88%）。

---

## Dependencies & Execution Order

- **Phase 1 (Setup)** → **Phase 2 (Foundational)** 為所有 US 的前置，**必須先完成**。
- **US1 (P1)** 依賴 Phase 2（T002/T003 模擬器、T004 ReauthRequired、T005 靜默續期、T007 client 接線）。
- **US2 (P2)** 僅依賴 Phase 1/2 的基線；**與 US1 多數獨立**（不需重連機制）→ 可在 US1 進行時平行開工。
- **US3 (P3)** 依賴 Phase 2 的韌性設定（T006）與 US1 的 `_with_reconnect`（T009，退避掛在其上）→ 排在 US1 之後。
- **Phase 6 (Polish)** 最後。
- 故事完成順序（MVP 優先）：**US1 → US2 → US3 → Polish**（US2 可與 US1 平行）。

## Parallel Opportunities

- **Phase 2**：T002、T003、T004、T005、T006 皆 [P]（不同檔/不同關注點）可平行；T007 依賴 T002/T003。
- **US1**：測試任務 T008、T010、T012、T014、T029 [P]（不同測試檔/案例）可先平行寫好（Red），再依序做實作 T009→T011→T013→T015。
- **US2 ⟂ US1**：US2（classifier/cli 快取重用）與 US1（imap_client 重連）多在不同關注點，可平行推進；交集在 `cli.py` 需留意合併。
- **T028（進度依迴圈性質啟動，FR-013）**：與韌性邏輯正交，**可在任何時點獨立先做**；唯與 US1/US2 同改 `cli.py`/`classifier.py`，合併時留意。
- **Polish**：T023、T024、T026 [P]。

## Implementation Strategy

- **MVP = US1**：先交付「中途過期/斷線自動恢復或乾淨停止」，這是 R7 的核心價值，獨立可測可發。
- 之後加 **US2**（效能：整夾讀一次）與 **US3**（健壯：退避＋可設定門檻）作增量。
- 全程 TDD（Red→Green→Refactor）、離線、對拍真 imaplib、雙層驗證；收尾跑覆蓋率閘門與 mypy。
- 完成後依憲法走 `/speckit.analyze`（gate）→ `/speckit.implement` → SR（APPROVE）→ 升版/CHANGELOG → 發版（含真實帳號 smoke 的 token 過期項）。
