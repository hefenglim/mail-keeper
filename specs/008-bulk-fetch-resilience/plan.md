# Implementation Plan: 大量信箱讀取的韌性與調校（Bulk Fetch Resilience & Tuning, Phase 3）

**Branch**: `008-bulk-fetch-resilience` | **Date**: 2026-06-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/008-bulk-fetch-resilience/spec.md`

## Summary

針對「仍需逐封標頭」的匯出/列標題讀取路徑（`list_headers`）三項改進：

- **P5 重連可續傳**：把 `list_headers` 從「整個被 `_with_reconnect` 包住（重連即整批重抓）」改為**自帶可續傳的韌性迴圈**——已取得標頭存於 `collected`（uid→MailHeader），重連後重新 `SELECT`+`UID SEARCH ALL`，與 `collected` 取差集只抓差集；UIDVALIDITY 變更則清空 `collected` 整批重抓。進度以 `len(collected)` 推進（跨重連延續）。
- **P6 FETCH 批量可調**：`config.json` 新增 `fetch_batch_size`（預設 50、下限 1，無效/缺漏退預設）；`OutlookIMAPClient` 以注入的批量值分批。
- **P7 標頭解析微優化**：讀標頭以 `email.parser.BytesHeaderParser`（只解析表頭）取代 `email.message_from_bytes`（整封）；`MailHeader` 輸出逐字不變。

技術取向（honor 憲法 Principle I）：續傳/批量/解析全是讀取協定細節，**只在 `imap_client.py`**；上層 cli 僅透過設定注入批量（領域中立 int），不認識 imaplib。**不新增 runtime 相依**。完成後升 **0.6.3**。分類路徑（006/007）完全不動。

## Technical Context

**Language/Version**: Python ≥ 3.10（鎖定）

**Primary Dependencies**: stdlib `imaplib` + `email`（含 `email.parser.BytesHeaderParser`）· MSAL · `charset-normalizer`。**不新增 runtime 相依**。

**Storage**: `config.json` 新增可選 `fetch_batch_size`。

**Testing**: `pytest` 全程離線。跨 seam 走 **IMAP 模擬器引擎**：`arm_expiry(before_op="fetch")` 注入中途斷線驗續傳「不整批重抓」（`command_counts["UID FETCH"]`≈⌈N/批⌉、不翻倍；`fetches_per_folder`/`redundant_full_folder_reads`）、`set_uidvalidity` 驗 UIDVALIDITY 變更重抓、`assert_all_fetches_request_uid`、最終 headers 完整/UID 全非空/無重複/無遺漏；`config_store` 驗 `fetch_batch_size` 解析；解析等價以母版多編碼主旨逐字比對。引擎已具所需注入（無需擴充）。

**Target Platform**: Windows / Linux / macOS 主控台 CLI。

**Project Type**: 單一專案 CLI（src layout）。

**Performance Goals**: 讀取 N 封、第 k 批後斷線重連 → 重抓標頭量趨近 0（已取得不重抓）；`fetch_batch_size`=M → FETCH 往返=⌈N/M⌉。

**Constraints**: 全程離線可測；不新增 runtime 相依；`mypy` 乾淨；secrets 不外洩；Backend Isolation；唯讀路徑維持 dry-run 預設；重連有界（沿用 `max_reconnect_attempts`）；標頭輸出逐字等價現況。

**Scale/Scope**: 數千～數萬封讀取；多次中斷皆續完。

## Constitution Check

*GATE: 必須於 Phase 0 前通過，Phase 1 設計後再次複查。*

| Principle | 遵循方式 | 結論 |
|---|---|---|
| I. Backend Isolation（NON-NEGOTIABLE）| 續傳/批量/解析之 IMAP 細節**只在 `imap_client.py`**；上層僅注入批量 int；不特例化後端、不 import imaplib。| ✅ Pass |
| II. OAuth-Only | 不動認證；續傳沿用既有重連/靜默續期。| ✅ Pass |
| III. Safe-by-Default | 唯讀讀取路徑；不動破壞性動作與 dry-run 預設。| ✅ Pass |
| IV. Secrets Never Leak | 新路徑不記錄/輸出 token。| ✅ Pass |
| V. Test-First（NON-NEGOTIABLE）| 全部 Red→Green、離線；跨 seam 走引擎（測請求端 + log 驗不整批重抓 + UID 不變量 + 等價）。| ✅ Pass |
| VI. Crash-Proof & Honest | 續傳有界（沿用 `max_reconnect_attempts`）；重連用盡如實外拋；升版 0.6.3 + 真實日期 CHANGELOG + 回填報告。| ✅ Pass |

**無違規** → Complexity Tracking 留空。

## Project Structure

### Documentation (this feature)

```text
specs/008-bulk-fetch-resilience/
├── plan.md · research.md · data-model.md · quickstart.md
├── contracts/   (list-headers-resumable.md, fetch-batch-config.md)
└── tasks.md     (/speckit.tasks 產出)
```

### Source Code (repository root)

```text
src/mailkeeper/
├── config.py        # 改：FETCH_BATCH_DEFAULT=50（取代模組私有 _FETCH_BATCH 預設）
├── config_store.py  # 改：Configuration 加 fetch_batch_size（_as_positive_int，預設 50、下限 1）
├── imap_client.py   # 改：__init__ 接受 fetch_batch_size；list_headers 改自帶可續傳韌性迴圈
│                    #     （collected 跨重連保留、re-SEARCH 取差集、UIDVALIDITY 變更重抓、進度延續）；
│                    #     新增 _current_uidvalidity()；讀標頭改 BytesHeaderParser（P7）
└── cli.py           # 改：建構 OutlookIMAPClient 時注入 fetch_batch_size（來自 config）

tests/
├── test_config_store.py          # 增：fetch_batch_size 解析（預設/無效退預設/下限）
├── test_imap_loop_regression.py  # 增：中途斷線續抓「不整批重抓」（FETCH 次數≈⌈N/批⌉）、UID 不變量、進度延續
├── test_imap_dataset.py / p2     # 增：UIDVALIDITY 變更重抓、多次斷線續完
└── test_decode.py / test_backend # 增：BytesHeaderParser 解析等價（多編碼主旨逐字一致）
```

**Structure Decision**: 沿用單一專案 src layout。改動集中於 `imap_client.py`（讀取路徑）+ config 兩檔 + cli 注入；不新增模組、不新增 runtime 相依。`list_headers` 由「外層 `_with_reconnect` 包整體」改為「內建可續傳迴圈」（其餘讀寫方法仍用 `_with_reconnect`）。

## Complexity Tracking

> 無憲法違規，無需填寫。
