# Specification Quality Checklist: 啟動選單與 CSV 郵件匯出／分類

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-06-21
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- 規格本文依使用者要求以繁體中文撰寫；結構標題與 FR-/SC-/US 標籤保留英文以利下游 `/speckit-plan`、`/speckit-tasks`、`/speckit-analyze` 解析。
- 2026-06-21 修訂：三功能串成單一 CSV 工作流 —— 功能1 產出含 `current_folder`＋可編輯 `target_folder` 的分類工作表、功能2 改為「所有資料夾清單」參考、功能3 比較 current vs target 只搬有變動者、先出初步檢查報告再經明確確認才搬（符合憲法 Principle III）。識別鍵＝(current_folder, uid)。
- 功能1 範圍已定：由使用者**選擇單一資料夾**後匯出；CSV 欄位擴充為「供 AI／LLM Agent 有效分類」（寄件者、收件者、主旨、日期、所在資料夾、內容摘要）＋可編輯 `target_folder`；外部 Classifier（人或 Agent）填寫 `target_folder`，MailKeeper 不內建 LLM。
- 已決議：功能1 的 CSV **只出標頭類欄位**（uid／所在資料夾／寄件者／收件者／主旨／日期）＋ target_folder，**不讀內文、不含 snippet**（較輕、較隱私）。後端只需擴充「列舉資料夾／讀指定資料夾標題／建立資料夾／跨資料夾搬移」，不需抓內文。
- 分期已明文：本期為「先驗證」的**手動流程**（CSV 由人＋AI 在工具外編輯，功能3 檢查＋確認才搬移）；**LLM 底層自動串接三功能屬未來階段、不在本期範圍**。
- FR-012／FR-014 提到「列舉資料夾／跨資料夾搬移」能力為行為層需求；其介面（`MailBackend` 擴充）屬 plan 階段，且仍須維持 `imaplib` 僅在 `imap_client.py`（憲法 Principle I）。
- 全項通過，無需在 `/speckit-clarify`／`/speckit-plan` 前修改。
- 2026-06-21 已套用 `/speckit-analyze` 全部修正：I1（`MailHeader.recipients` 附加在最後，修 data-model 欄序與 T002/T003）、I2（`move` 來源參數統一為 `mailbox`）、U1（FR-011／T014/T017：執行時來源 UID 已不存在列為逐列失敗）、C1（新增「匯出預設覆寫」假設）、A1（資料夾清單本期只出 `folder`、T010 註明）、C2（新增 T026 機密不外洩負向測試）、T1（US3 重編號為 T013–T015 測試／T016–T018 實作）。
- 2026-06-21 第二輪 `/speckit-analyze` 修正：C1（版本措辭統一「0.3.0 → 0.4.0」）、I1（plan 補 `tests/test_backend.py`）、U1（新增 **FR-016** 覆寫 + 測試 T027）、U2（FR-002 明列子指令/印用法兩條路徑）、A1（大量郵件/分批標為本期 out-of-scope/未來 R7）、FR-012/SC-006（新增路徑錯誤測試 T028）、A2（順序相依 edge case 加「見 FR-011」）、T2（FakeBackend `move` 改用 `dest_folder`）。任務數 → 28（T001–T028）。
- 2026-06-21 第三輪 `/speckit-analyze` 強化：**I2（CSV 表頭改全英文 `uid,current_folder,target_folder,date,from,to,subject`，spec/data-model/contracts 三處同步；反轉先前中文欄名）**、C2（T005 增中/英/日/韓/阿拉伯文 + emoji 多語文斷言對齊 SC-002）、A1（Assumptions 加「不重排列序」）、I1（data-model `move` 第二參數 `dest`→`dest_folder`）、C1（T004 註記沿用 `ensure_folder`）。
- 2026-06-22 第四輪 `/speckit-analyze`：修 **I1（tasks T006 + US1 Independent Test 漏改的中文表頭→英文）**；補 **C1**（T019 加「子指令於非 TTY 跑完不阻塞」斷言）、**A1**（T017 明示目標資料夾預設自動建立、建立失敗才不可行）。並補齊 analyze 未點到的字面表頭殘留：`research.md` D4、`plan.md` Key Decision 4、`spec.md` Key Entities。全樹確認**無中文字面表頭殘留**，CSV 表頭一致為 `uid,current_folder,target_folder,date,from,to,subject`。
- 2026-06-22 第五輪 `/speckit-analyze`：**C2** 資料夾清單 `count` 措辭一致化 —— 統一為「本期僅 `folder`、`count` 列為未來」，同步 spec FR-004／US2 body／Key Entities、contracts §3、tasks US2 Goal／T009（先前漏改的 `folder[,count]`）；data-model 已正確。**C1** T027 改為「分別斷言 `write_worksheet` 與 `write_folders` 皆覆寫」。全樹 grep 確認 `count`/`郵件數` 與 CSV 表頭皆一致。
