# Requirements Quality Checklist: Bulk Classify Efficiency — Phase 1 (Lean Existence Check)

**Purpose**: implement 前的「需求品質」閘門——測 spec 的需求是否完整、清晰、一致、可量測、涵蓋邊界（非測實作）。
**Created**: 2026-06-28
**Feature**: [spec.md](../spec.md)

**Note**: 由 `/speckit.checklist` 產生。每項檢查的是「需求寫得對不對」，不是「程式跑得對不對」。深度=標準；對象=作者/審查者。

## Requirement Completeness

- [ ] CHK001 是否明確定義「存在性判斷所需的最小資訊」＝來源夾現存 UID 集合、不含郵件內容？ [Completeness, Spec §FR-001]
- [ ] CHK002 已標 `\Deleted` 未 expunge 郵件是否被明確納入「現存」定義？ [Completeness, Spec §FR-001, Clarify Q1]
- [ ] CHK003 「每來源夾每流程最多查一次」是否同時涵蓋報告與執行兩階段（共用快取）？ [Completeness, Spec §FR-002]
- [ ] CHK004 是否明列「必須不受影響」的既有路徑（匯出/列標題/搬移執行）？ [Completeness, Spec §FR-006]
- [ ] CHK005 交付後的追蹤義務（回填效能報告狀態表＋版本號＋同步 CHANGELOG/backlog）是否被需求化？ [Completeness, Spec §Follow-up]

## Requirement Clarity

- [ ] CHK006 「現存」是否定義明確（哪些郵件算數）而非留白？ [Clarity, Spec §FR-001]
- [ ] CHK007 單次往返查詢下的 determinate 進度期望是否清楚（總數=該夾郵件數、推進至完成、不注入延遲）？ [Clarity, Spec §FR-005, Clarify Q2]
- [ ] CHK008 「等價現況」是否給了具體比對基準（同一工作表 → 逐列判定相同）？ [Clarity, Spec §FR-003]
- [ ] CHK009 後端中立能力是否以 WHAT 表述（不指名協定/指令）？ [Clarity, Spec §FR-007]

## Requirement Consistency

- [ ] CHK010 存在性最小化（FR-001）與內容功能無回歸（FR-006）是否界線清楚、不衝突（最小化僅限分類存在性）？ [Consistency, Spec §FR-001/§FR-006]
- [ ] CHK011 「已標刪視為存在」（Clarify Q1）與「判定 100% 一致」（SC-002）是否彼此相容？ [Consistency, Spec §SC-002]
- [ ] CHK012 跨 spec/plan/contracts 術語是否一致（現存集合 / Source Folder Presence Set / `list_uids`）？ [Consistency]

## Acceptance Criteria Quality / Measurability

- [ ] CHK013 「不再整夾抓標頭」是否可客觀量測（操作日誌計數＝0）？ [Measurability, Spec §SC-001]
- [ ] CHK014 「下載量降 ≥90%」是否有明確量測基準（bytes、同情景前後對照）？ [Measurability, Spec §SC-003, §Assumptions]
- [ ] CHK015 「逐列判定 100% 一致」是否可對既定基準驗證？ [Measurability, Spec §SC-002]
- [ ] CHK016 「進度不退化」是否表述為可檢查條件（determinate、達 total）？ [Measurability, Spec §SC-006]

## Scenario & Edge Coverage

- [ ] CHK017 是否涵蓋「報告時存在、執行時已不存在」（逐列失敗不變）？ [Coverage, Spec §Edge Cases]
- [ ] CHK018 是否涵蓋「存在性查詢期間連線中斷/權杖過期」（重連、不重複/不遺漏）？ [Coverage, Spec §FR-009]
- [ ] CHK019 是否涵蓋超大夾（>10k）仍為單次最小查詢、不退化為整夾內容分批下載？ [Coverage, Spec §Edge Cases]
- [ ] CHK020 是否明定「內容路徑不得被誤最小化」（匯出/列標題仍取完整標頭）的邊界？ [Coverage, Spec §FR-006/§Edge Cases]

## Non-Functional (Security / Architecture / Resilience)

- [ ] CHK021 新能力是否重申祕密安全（token 不入日誌/輸出、OAuth 範圍不變）？ [Coverage, Spec §FR-008]
- [ ] CHK022 後端隔離是否被需求化（協定細節不跨 seam、上層不特例化後端）？ [Consistency, Spec §FR-007, Constitution I]
- [ ] CHK023 重連相容是否明定為「重用既有有界重試」而非新機制？ [Clarity, Spec §FR-009]

## Dependencies & Assumptions

- [ ] CHK024 「後端能以單一查詢回傳資料夾 UID 集合」假設是否陳述並可驗證（IMAP SEARCH + FakeBackend）？ [Assumption, Spec §Assumptions]
- [ ] CHK025 對既有 `ClassifyCache`「每夾一次」語意的依賴是否載明？ [Dependency, Spec §Assumptions]

## Ambiguities & Conflicts / Scope

- [ ] CHK026 P4（候選分組）是否明確排除本期並附緣由，避免範圍歧義？ [Scope, Spec §Out of Scope]
- [ ] CHK027 是否因延後 P4 而避免了「重排序下結果集合一致」的潛在矛盾 SC？ [Conflict, Spec §SC-004]
- [ ] CHK028 是否避免任何 [NEEDS CLARIFICATION] 殘留、所有決策皆已收斂於 Clarifications？ [Ambiguity, Spec §Clarifications]

## Notes

- 完成檢查打勾 `[x]`，發現問題就地註記。
- 這份清單供 `/speckit.analyze` 與 SR 交叉對照；任一項「需求未涵蓋/不清/不一致」即為 implement 前應修的缺口。
