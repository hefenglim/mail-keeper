# Requirements Quality Checklist: Bulk Fetch Resilience & Tuning — Phase 3

**Purpose**: implement 前的「需求品質」閘門——測 spec 需求是否完整、清晰、一致、可量測、涵蓋邊界（非測實作）。
**Created**: 2026-06-29
**Feature**: [spec.md](../spec.md)

## Requirement Completeness
- [ ] CHK001 是否定義重連後「只抓差集、不重抓已取得批次」的續傳行為？ [Completeness, Spec §FR-001]
- [ ] CHK002 UIDVALIDITY 變更的處理（捨棄進度、整批重抓）是否定義？ [Completeness, Spec §FR-002]
- [ ] CHK003 最終結果不變量（完整、UID 全非空、無重複、無遺漏）是否需求化？ [Completeness, Spec §FR-003]
- [ ] CHK004 FETCH 批量 config 的鍵名/預設/界限/無效退預設是否完整？ [Completeness, Spec §FR-004]
- [ ] CHK005 交付後追蹤（回填報告 P5/P6/P7、升版、同步文件）是否需求化？ [Completeness, Spec §Follow-up]

## Requirement Clarity
- [ ] CHK006 「續傳」是否清楚（重新 SEARCH ALL 取差集），非模糊「從中斷處」？ [Clarity, Spec §FR-001/§Clarifications]
- [ ] CHK007 進度跨重連「延續、不歸零、不倒退」是否明確？ [Clarity, Spec §FR-007]
- [ ] CHK008 「只解析表頭」與「輸出逐字等價」是否清楚界定？ [Clarity, Spec §FR-005]
- [ ] CHK009 批量界限（預設 50、下限 1）是否明確可測？ [Clarity, Spec §FR-004]

## Requirement Consistency
- [ ] CHK010 續傳的有界重連是否與既有 `max_reconnect_attempts`（feature 005）一致、非新機制？ [Consistency, Spec §Dependencies]
- [ ] CHK011 P7 解析改動與「輸出等價、分類路徑無回歸」是否相容不衝突？ [Consistency, Spec §FR-005/§FR-006]
- [ ] CHK012 術語一致（讀取進度／fetch_batch_size／只解析表頭）跨 spec/plan/contracts？ [Consistency]

## Acceptance Criteria / Measurability
- [ ] CHK013 「重抓量趨近 0」是否可客觀量測（`UID FETCH` 次數不翻倍、≤⌈N/批⌉+1）？ [Measurability, Spec §SC-001]
- [ ] CHK014 「批量生效」是否可量測（FETCH 往返=⌈N/M⌉）？ [Measurability, Spec §SC-003]
- [ ] CHK015 「解析等價」是否可對母版逐字驗證？ [Measurability, Spec §SC-004]
- [ ] CHK016 「UIDVALIDITY 變更安全重抓」是否可注入驗證？ [Measurability, Spec §SC-002]

## Scenario & Edge Coverage
- [ ] CHK017 是否涵蓋尾端斷線（只續最後批）、多次斷線（每次續）、重連用盡（如實外拋）？ [Coverage, Spec §Edge Cases]
- [ ] CHK018 是否涵蓋空夾/單批可容（續傳退化為無操作）與批量極端值？ [Coverage, Spec §Edge Cases]
- [ ] CHK019 是否明定唯讀路徑、不影響分類/搬移與 dry-run？ [Coverage, Spec §FR-006/§FR-009]

## Non-Functional (Security / Architecture)
- [ ] CHK020 後端隔離是否需求化（續傳/批量/解析細節不跨 seam、上層只注入 int）？ [Consistency, Spec §FR-008, Constitution I]
- [ ] CHK021 祕密安全是否重申（token 不入日誌/輸出）？ [Coverage, Spec §FR-009]

## Dependencies & Assumptions
- [ ] CHK022 「引擎已具 arm_expiry/set_uidvalidity/log、無需擴充」假設是否陳述？ [Assumption, Spec §Dependencies, research §D4]
- [ ] CHK023 對 feature 005（重連）、現行 list_headers 分批的依賴是否載明？ [Dependency, Spec §Dependencies]

## Ambiguities / Scope
- [ ] CHK024 是否無 [NEEDS CLARIFICATION] 殘留、三決策（續傳粒度/批量/進度）皆收斂於 Clarifications？ [Ambiguity, Spec §Clarifications]
- [ ] CHK025 範圍是否明確（006/007 已完成不重做、後端切換/OAuth/dry-run/C3 out-of-scope）？ [Scope, Spec §Out of Scope]

## Notes
- 供 `/speckit.analyze` 與 SR 交叉對照；重點：CHK001/006（續傳機制）、CHK002/016（UIDVALIDITY）、CHK011/015（P7 等價）。
