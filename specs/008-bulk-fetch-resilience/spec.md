# Feature Specification: Bulk Fetch Resilience & Tuning — Phase 3 (P5 resumable reconnect + P6 configurable FETCH batch + P7 lean header parse)

**Feature Branch**: `008-bulk-fetch-resilience`

**Created**: 2026-06-29

**Status**: Draft

**Input**: User description: 承接 006（P1，v0.6.1）、007（P4+P3+P2+C1，v0.6.2）。本期針對仍需逐封標頭的**匯出/列標題讀取路徑**：P5 重連可續傳（不整批重抓）、P6 FETCH 批量可調、P7 標頭解析微優化。完成後進版 0.6.3。

> 對應 `doc/mailkeeper-performance-report-20260627.html` 的 **P5 / P6 / P7**。分類路徑（006/007）不在本期。backlog C3 不在本期。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 大型信箱讀取於中斷後從中斷處續完（Priority: P1）

使用者匯出大型資料夾的工作表（功能1）或列出標題（功能2），過程以分批讀取標頭。若中途連線中斷或存取權杖過期，使用者期望系統透明重連後**從中斷處繼續**，而不是把先前已讀的全部丟棄、從第一批重來——尤其大信箱在尾端斷線時，重來代價極高且更易再次被打斷。

**Why this priority**: 這是本期最大價值。現況 `list_headers` 整個被透明重連包住 → 重連即整批重抓。對上萬封信箱，尾端斷線的重來是 O(n) 白工。

**Independent Test**: 對大型資料夾讀取標頭、於第 k 批後注入中斷並透明重連；以操作日誌驗證「已取得批次不再重抓」（重抓量趨近 0），且最終標頭完整、UID 全非空、無重複、無遺漏。

**Acceptance Scenarios**:

1. **Given** 一個大型來源資料夾、讀取至第 k 批後連線中斷，**When** 透明重連後續抓，**Then** 已取得的前 k 批不再重抓（只續抓剩餘），最終標頭集合與「全程不中斷」取得者等價（完整、UID 全非空、無重複/遺漏）。
2. **Given** 重連後該資料夾的 UIDVALIDITY 已變更（信箱重建、UID 空間失效），**When** 系統偵測到變更，**Then** 安全地重新抓取（捨棄過時進度、不沿用過時 UID），結果仍正確。
3. **Given** 讀取全程（含重連期間），**When** 觀察輸出，**Then** 持續顯示進度、不像當機。

---

### User Story 2 - 每批讀取封數可由設定調整（Priority: P2）

進階使用者面對不同伺服器/網路條件，期望能調整「每批讀取封數」以最佳化大信箱的讀取速度（調大減少往返），而非被寫死的值限制。

**Why this priority**: 低成本的可調性，沿用既有韌性設定外部化（feature 005）的精神；對大信箱吞吐有實感，但非每位使用者必需。

**Independent Test**: 於 config.json 設定每批封數，對同一資料夾讀取，驗證讀取往返數＝⌈總數/設定批量⌉；設無效值時退回安全預設且不崩潰。

**Acceptance Scenarios**:

1. **Given** config.json 設定每批封數為 M，**When** 讀取 N 封標頭，**Then** 讀取往返數為 ⌈N/M⌉。
2. **Given** config.json 的批量值無效（非正整數、型別錯誤）或缺漏，**When** 讀取，**Then** 退回安全預設、永不崩潰。

---

### User Story 3 - 標頭解析更輕量且輸出零回歸（Priority: P3）

使用者期望讀取大信箱時更省資源，且匯出/列標題的內容（含各種編碼）與既有完全一致。

**Why this priority**: 微優化（只解析表頭而非整封），對超大信箱省 CPU；主要價值是「不回歸」的保證。

**Independent Test**: 對含多種編碼主旨（ASCII/CJK/emoji/encoded-word/折行）的信箱讀取標頭，驗證輸出逐字與優化前一致。

**Acceptance Scenarios**:

1. **Given** 含多種編碼主旨的資料夾，**When** 讀取標頭，**Then** 每封的 UID、主旨、寄件者、收件者、日期（含解碼）與優化前逐字一致。

---

### Edge Cases

- **尾端斷線**：最後一批前斷線 → 只續抓最後剩餘批次，不重抓前面。
- **多次斷線**：讀取期間多次中斷 → 每次皆從當前進度續抓（有界重連次數內）。
- **UIDVALIDITY 變更**：重連後資料夾被重建 → 安全重抓（不沿用過時 UID）。
- **空資料夾 / 單批可容**：N ≤ 批量 → 一批取完；續傳邏輯退化為無操作。
- **批量設定極端值**：1 或極大值 → 仍正確（極大則等同一次取完）；無效值退預設。
- **重連用盡**：超過有界重連次數仍失敗 → 如實往外傳（不靜默產出不完整標頭）。
- **內容路徑唯讀**：讀取不變更信箱；不影響分類/搬移。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `list_headers`（匯出/列標題用）於連線中斷／重連後 MUST 從中斷處續抓——只取尚未取得的郵件標頭，MUST NOT 重抓已取得的批次。
- **FR-002**: 重連後若來源資料夾 UIDVALIDITY 變更，系統 MUST 安全地重新抓取（捨棄過時進度、不沿用過時 UID），不得產生錯亂或不完整結果。
- **FR-003**: 續抓後最終標頭集合 MUST 完整、每筆 UID 非空、無重複、無遺漏，與「全程不中斷」取得者等價。
- **FR-004**: 每批 FETCH 的封數 MUST 可由 config.json 設定；無效／缺漏值 MUST 退回安全預設、永不崩潰（比照 feature 005 韌性設定的解析）。
- **FR-005**: 讀標頭 MUST 以「只解析表頭」的輕量路徑處理；輸出的 `MailHeader` 各欄位（含編碼解碼）MUST 與優化前逐字一致。
- **FR-006**: 匯出工作表、列出標題、以及任何依賴 `list_headers` 的路徑，其輸出 MUST 與優化前逐字一致；分類路徑（006/007）MUST NOT 受影響。
- **FR-007**: 讀取全程（含恢復／重連期間）MUST 持續顯示進度，進度回報語意不退化。
- **FR-008**: 續抓、批量、解析等 IMAP/協定細節 MUST 僅存在於 `imap_client.py`；上層（cli/organizer）MUST NOT 特例化任一後端、MUST NOT 讓協定細節跨越後端邊界。
- **FR-009**: 本變更 MUST NOT 記錄或輸出任何存取權杖／祕密；唯讀讀取路徑 MUST 維持破壞性動作的 dry-run 預設不變。

### Key Entities *(include if feature involves data)*

- **讀取進度（Fetch Progress）**：一次 `list_headers` 中「已取得標頭的 UID 集合」＋目標 UID 清單，用於重連後計算「尚未取得」的續抓範圍（FR-001/003）。
- **每批讀取封數設定（Fetch Batch Size）**：可由 config.json 覆寫、具安全預設與界限的整數（FR-004）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 讀取 N 封、於第 k 批後連線中斷並透明重連，「重抓」的標頭量趨近於 **0**（已取得批次不重抓，可由操作日誌驗證）；最終標頭完整、UID 全非空、無重複/遺漏。
- **SC-002**: 來源夾 UIDVALIDITY 於重連後變更 → 系統安全重抓、結果正確（無過時 UID 沿用）。
- **SC-003**: config.json 設定每批封數 M → 讀取往返數＝**⌈N/M⌉**；無效值退安全預設、不崩潰。
- **SC-004**: 標頭內容（ASCII/CJK/emoji/encoded-word/折行還原）優化前後一致率 **100%**。
- **SC-005**: 匯出工作表／列出標題輸出優化前後一致率 **100%**；分類路徑（006/007）無回歸。
- **SC-006**: 讀取全程（含重連期間）皆有進度回饋，無「無回饋、看似當機」的等待區段。

## Assumptions

- 續傳以「重連後重新取得該夾現存 UID 清單，與『已取得 UID 集合』取差集，只抓差集」實現（過渡決策；確切粒度／是否以游標推進於 `/speckit.clarify` 細化）。
- 每批封數的 config 鍵名／安全預設（沿用現行 50）／界限（如下限 1）為過渡值，於 `/speckit.clarify` 確認。
- 進度跨重連延續（已完成數不歸零）。
- UIDVALIDITY 變更以 SELECT 回應的 UIDVALIDITY 偵測；變更即重抓。
- 本期僅動唯讀讀取路徑（`list_headers`）；不改分類/搬移、dry-run 預設、OAuth；不含 backlog C3。

## Dependencies

- 既有透明重連／有界重試（feature 005 / R7）與 `list_headers` 分批 FETCH。
- IMAP 模擬器引擎（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`）作為唯一核可的跨 seam 測試工具：`arm_expiry`（中途斷線）、`set_uidvalidity`（UIDVALIDITY 變更）、`loop_report`（fetches_per_folder/redundant_full_folder_reads/command_counts/roundtrips）、`assert_all_fetches_request_uid`、`snapshot`。

## Out of Scope (this phase)

- 分類存在性最小化（P1，006）、分類搬移效能與冪等（P4/P3/P2/C1，007）。
- 後端切換（仍 IMAP）、OAuth／XOAUTH2 變更、破壞性動作 dry-run 預設變更。
- backlog C3（`_unfold`/`_decode` 去前導折疊空白）。

## Follow-up after delivery (tracking)

- 回填 `doc/mailkeeper-performance-report-20260627.html` 狀態表：**P5/P6/P7 → ✅ 已完成 v0.6.3**；更新卡片、最後更新、修訂紀錄。
- 同步 `CHANGELOG.md`（升版 0.6.3）與 `memory/roadmap-backlog.md`、`memory/perf-optimization-report.md`。
