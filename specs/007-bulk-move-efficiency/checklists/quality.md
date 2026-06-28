# Requirements Quality Checklist: Bulk Move Efficiency & Idempotency — Phase 2

**Purpose**: implement 前的「需求品質」閘門——測 spec 的需求是否完整、清晰、一致、可量測、涵蓋邊界（非測實作）。
**Created**: 2026-06-29
**Feature**: [spec.md](../spec.md)

**Note**: 由 `/speckit.checklist` 產生；檢查「需求寫得對不對」，非「程式跑得對不對」。深度=標準；對象=作者/審查者。

## Requirement Completeness

- [ ] CHK001 是否明確定義分組鍵為 (來源夾, 目標夾)、且輸出依 CSV 列序？ [Completeness, Spec §FR-001/§FR-002]
- [ ] CHK002 批次部分失敗的「退回逐封歸因」是否被需求化（成功者仍搬、失敗者回報、不連坐）？ [Completeness, Spec §FR-005]
- [ ] CHK003 後備搬移冪等所需的「目標既有複本偵測」是否完整定義（含無 Message-ID 的退路）？ [Completeness, Spec §FR-006, research §D4]
- [ ] CHK004 重連／權杖過期於搬移中途的「從中斷處續完」是否定義（不重複、不遺漏）？ [Completeness, Spec §FR-007]
- [ ] CHK005 交付後追蹤義務（回填報告 P2/P3/P4＋C1/C2、升版、同步文件）是否被需求化？ [Completeness, Spec §Follow-up]

## Requirement Clarity

- [ ] CHK006 「提前停止」是否清楚定義為連線層級失敗（重連用盡），且單列資料失敗明確不計入？ [Clarity, Spec §FR-013/§Clarifications]
- [ ] CHK007 批次大小規則是否明確（整群一批、超過固定上限分塊、不開放設定）？ [Clarity, Spec §FR-004/§FR-014]
- [ ] CHK008 「免重複 SELECT」的觸發條件是否明確（未選/夾不同/模式不同；連線/重連重置）？ [Clarity, Spec §FR-003]
- [ ] CHK009 批次搬移的進度推進規則是否明確（每批 += 該批封數、達 total）？ [Clarity, Spec §FR-009]
- [ ] CHK010 「等價現況」是否給具體比對基準（結果集合、每列成敗、輸出順序）？ [Clarity, Spec §FR-008]

## Requirement Consistency

- [ ] CHK011 早停改連線層級（FR-013）與「結果集合等價現況」（FR-008）是否相容（早停語意改變下如何界定等價）？ [Consistency, Spec §FR-008/§FR-013]
- [ ] CHK012 後備冪等（FR-006）與既有安全鐵則（COPY 成功才標刪、UID EXPUNGE 限定）是否一致不衝突？ [Consistency, Spec §FR-010]
- [ ] CHK013 跨 spec/plan/contracts 術語是否一致（move_many／搬移群組／選取狀態／Message-ID 去重）？ [Consistency]

## Acceptance Criteria Quality / Measurability

- [ ] CHK014 「SELECT N→1」是否可客觀量測（redundant_selects()==0、SELECT 計數）？ [Measurability, Spec §SC-001]
- [ ] CHK015 「搬移往返 N→⌈N/批⌉」是否可量測（指令日誌 MOVE 次數）？ [Measurability, Spec §SC-002]
- [ ] CHK016 「後備重試目標複本數正好 1」是否可客觀驗證（snapshot 前後）？ [Measurability, Spec §SC-005]
- [ ] CHK017 「不連坐他人 \Deleted」是否可量測（被波及次數=0）？ [Measurability, Spec §SC-007]
- [ ] CHK018 「批次部分失敗逐封歸因等價」是否可對既定基準驗證？ [Measurability, Spec §SC-004]

## Scenario & Edge Coverage

- [ ] CHK019 是否涵蓋「同來源多目標」（分組以 (來源,目標) 為鍵、各群各批）？ [Coverage, Spec §Edge Cases]
- [ ] CHK020 是否涵蓋「超大同群分塊」（超過上限→多批、各批結果與單批等價）？ [Coverage, Spec §Edge Cases/§FR-014]
- [ ] CHK021 是否涵蓋「COPY 後、標刪前」中斷的冪等子窗口（非僅 store-done 窗口）？ [Coverage, research §D4]
- [ ] CHK022 是否涵蓋「來源 UID 執行時已不存在」單列失敗、不連坐、不早停？ [Coverage, Spec §SC-010/§Edge Cases]
- [ ] CHK023 是否涵蓋「連線中斷且重連用盡」觸發提前停止並回報已完成/未完成？ [Coverage, Spec §FR-013]

## Non-Functional (Security / Architecture / Resilience)

- [ ] CHK024 後端隔離是否被需求化（move_many/選取追蹤/後備冪等之協定細節不跨 seam、上層不特例化後端）？ [Consistency, Spec §FR-011, Constitution I]
- [ ] CHK025 安全是否重申（dry-run 預設、UID 限定清除、secrets 不外洩）？ [Coverage, Spec §FR-010/§FR-012]
- [ ] CHK026 重連相容是否明定為重用既有有界重連，而非新機制？ [Clarity, Spec §FR-007/§Dependencies]

## Dependencies & Assumptions

- [ ] CHK027 「後端能批次搬移 UID 集合」「能查目標 Message-ID」假設是否陳述並可由引擎驗證（含引擎 HEADER 搜尋前置）？ [Assumption, Spec §Assumptions, research §D4]
- [ ] CHK028 對 feature 006（list_uids/快取）與 005（透明重連）與 0.5.1（破壞性安全）的依賴是否載明？ [Dependency, Spec §Dependencies]

## Ambiguities & Conflicts / Scope

- [ ] CHK029 是否避免任何 [NEEDS CLARIFICATION] 殘留、早停/批次/進度三決策皆收斂於 Clarifications？ [Ambiguity, Spec §Clarifications]
- [ ] CHK030 範圍是否明確（P5/P6/P7、後端切換、dry-run 預設變更 out-of-scope）？ [Scope, Spec §Out of Scope]

## Notes

- 本清單供 `/speckit.analyze` 與 SR 交叉對照；任一項「需求未涵蓋/不清/不一致」即為 implement 前應修的缺口。
- 重點關注：CHK006/011（早停語意一致性）、CHK021（C1 完整窗口）、CHK002/018（批次部分失敗歸因）。
