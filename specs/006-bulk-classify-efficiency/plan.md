# Implementation Plan: 大量信箱分類效能 — 第一期（Bulk Classify Efficiency, Phase 1: Lean Existence Check）

**Branch**: `006-bulk-classify-efficiency` | **Date**: 2026-06-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/006-bulk-classify-efficiency/spec.md`

## Summary

單一效能優化，功能與安全等價現況：

**P1 — 存在性檢查最小化**：分類「檢查報告」判斷來源郵件是否存在時，改為只取「來源夾現存 UID 集合」（一次最小化查詢），不再為了存在性下載並解析整夾完整標頭。實測現況對 10,000 封來源夾＝200 批 FETCH＋下傳 ~1.63MB 後幾乎全丟；改後＝1 次查詢、下載量降 ≥90%。「現存」沿用現況語意（含已標 `\Deleted` 未 expunge 者）。

技術取向（honor 憲法 Principle I 後端隔離）：新增能力「取得資料夾現存 UID 集合」以**後端中立**方式擴充 `MailBackend` 協定（`list_uids`），由各後端實作；IMAP 細節（`UID SEARCH ALL`）**只在 `imap_client.py`**，跨 seam 只傳領域型別（`set[str]` UID）。分類層只改 `_source_uids`（存在性來源由 `list_headers` 萃取 uid 改為直接 `list_uids`），**不動搬移執行路徑**（`execute` 不變）。**不新增任何 runtime 相依**。

> **本期不含 P4 候選分組**（經 `/speckit.clarify` 後分析決定延至 P2/P3：在 move 路徑未改前 P4 無往返效益、且分組會改變「連續失敗早停」的處理集合，宜與批次搬移/免重選同期設計）。詳見 spec「Out of Scope」與 research「Deferred」。

## Technical Context

**Language/Version**: Python ≥ 3.10（鎖定）

**Primary Dependencies**: stdlib `imaplib` + `email` · MSAL（OAuth2/XOAUTH2）· `charset-normalizer`。**本期不新增 runtime 相依**。

**Storage**: 無新增設定；`config.json` / `token_cache.bin` 既有不變。

**Testing**: `pytest`，全程離線。跨 seam 的 `list_uids` 走 **IMAP 模擬器引擎**（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`）實測請求端（送出 `UID SEARCH ALL`、不送整夾 header FETCH），以 `loop_report()`／`fetches_per_folder`／`command_counts`／`bytes_*` 驗證往返與下載下降；分類層邏輯（判定等價、每夾一次）以 `FakeBackend` 驗證；含重連（`arm_expiry`）異常路徑。

**Target Platform**: Windows / Linux / macOS 主控台 CLI。

**Project Type**: 單一專案 CLI（src layout）。

**Performance Goals**: 10,000 封來源夾的分類檢查報告 → 整夾完整標頭 FETCH = **0 次**；任一來源夾現存查詢/流程 = **1 次**；報告階段下載量較現況降 **≥90%**。

**Constraints**: 全程離線可測；不新增 runtime 相依；`mypy` 乾淨；secrets 永不記錄/外洩；Backend Isolation（imaplib 只在 `imap_client.py`）；`list_uids` 與既有透明重連/有界重試相容；破壞性動作維持 dry-run 預設；報告逐列判定、搬移結果與輸出順序、匯出/列標題輸出**等價現況**。

**Scale/Scope**: 信箱規模數千～數萬封（超大信箱串流/分頁不在本期）。

## Constitution Check

*GATE: 必須於 Phase 0 前通過，Phase 1 設計後再次複查。*

| Principle | 本 feature 的遵循方式 | 結論 |
|---|---|---|
| I. Backend Isolation（NON-NEGOTIABLE）| 新能力 `list_uids` 加在 `MailBackend` 協定；`UID SEARCH ALL` 等 IMAP 細節**只在 `imap_client.py`**；跨 seam 僅傳 `set[str]`（UID 領域識別）。`classifier`/`cli` 不特例化後端、不 import imaplib。| ✅ Pass |
| II. OAuth-Only | 不動認證；`list_uids` 沿用既有連線/授權。| ✅ Pass |
| III. Safe-by-Default | 分類維持 dry-run 預設；不動搬移路徑；存在性查詢為唯讀。| ✅ Pass |
| IV. Secrets Never Leak | `list_uids` 不記錄/輸出 token；UID 集合非機密。| ✅ Pass |
| V. Test-First（NON-NEGOTIABLE）| 全部 Red→Green、離線；跨 seam 走模擬器引擎（測請求端 + log 驗往返/下載下降 + 重連異常路徑）；分類邏輯走 FakeBackend。| ✅ Pass |
| VI. Crash-Proof & Honest | `list_uids` 以 `_with_reconnect` 包裝（有界重試、唯讀冪等）；進度全程不退化；交付升版 + 真實日期 CHANGELOG + 回填效能報告狀態表。| ✅ Pass |

**無違規** → Complexity Tracking 留空。

## Project Structure

### Documentation (this feature)

```text
specs/006-bulk-classify-efficiency/
├── plan.md              # 本檔
├── research.md          # Phase 0 決策（含 P4 Deferred 緣由）
├── data-model.md        # Phase 1 實體
├── quickstart.md        # Phase 1 驗證指南
├── contracts/           # Phase 1 介面契約（後端 list_uids + 分類存在性來源）
└── tasks.md             # /speckit.tasks 產出（非本指令）
```

### Source Code (repository root)

```text
src/mailkeeper/
├── organizer.py       # 改：MailBackend 協定新增 list_uids(folder, *, on_progress=None) -> set[str]（向後相容、後端中立）
├── imap_client.py     # 改：新增 list_uids + _list_uids_impl（select readonly + UID SEARCH ALL → set[str]，含 \Deleted；_with_reconnect 包裝；以 UID 數驅動 determinate 進度）
└── classifier.py      # 改：_source_uids 改呼叫 backend.list_uids（不再整夾抓標頭）；進度標籤改為「檢查現存」語意。execute／搬移路徑不動。

tests/
├── conftest.py                  # 改：FakeBackend.list_uids（in-memory UID 集合，驅動 on_progress）
├── test_backend.py              # 增：list_uids 經模擬器引擎—送出 UID SEARCH ALL、不整夾 FETCH、含 \Deleted、重連後完成、下載量遠低於 list_headers
├── test_classifier.py          # 改/增：_source_uids 用 list_uids；判定等價（含 deleted 視為存在）；每夾一次
└── test_imap_loop_regression.py# 增：分類報告 fetches_per_folder 整夾 header FETCH=0、UID SEARCH 出現、bytes_out 大降；搬移結果/輸出順序不變
```

**Structure Decision**: 沿用既有單一專案 src layout。改動落在 3 個既有產品模組（`classifier` 僅改 `_source_uids`），**不新增模組、不新增 runtime 相依、不動搬移執行路徑**。新能力以後端協定方法實作，維持 `MailBackend` 介面以領域型別跨 seam 與 Backend Isolation。

## Complexity Tracking

> 無憲法違規，無需填寫。
