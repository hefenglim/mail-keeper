# Implementation Plan: 大量信箱的效能與韌性（Bulk Resilience, R7）

**Branch**: `005-bulk-resilience` | **Date**: 2026-06-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/005-bulk-resilience/spec.md`

## Summary

讓大量郵件的匯出（功能1）與分類搬移（功能3）在「操作中途 OAuth token 過期或連線中斷」時仍能可靠完成：偵測到 session 失效/斷線時，於後端**透明重連**（沿用既有授權靜默續期 → 重建 IMAP 連線 → 重新認證 → 重試該操作），分類以**項目級續做**、匯出以**整批重抓**恢復；靜默續期不可行時**乾淨停止並回報已完成/未完成數**（使用者重新登入後重跑同一份工作表續完，冪等）。同時消除同一分類流程對同一來源夾的**重複整夾讀取**（報告階段讀一次當權威快取、執行重用），把暫時性錯誤以**有界退避重試**吸收，並讓韌性門檻**可由設定調整**。

技術取向（honor 憲法 Principle I 後端隔離）：重連/重試屬 IMAP 協定細節，**只放在 `imap_client.py`**；靜默續期屬 MSAL，**放在 `auth.py`**；`OutlookIMAPClient` 透過**注入的 token 提供者 callable** 在重連時取得新 token（不 import MSAL）。上層（`classifier`/`cli`）僅透過 `MailBackend` 介面、領域型別與後端中立回呼（`on_progress`/`on_status`）參與，不認識 imaplib 或 token 過期。**不新增任何 runtime 相依**（退避用 stdlib `time.sleep`）。

## Technical Context

**Language/Version**: Python ≥ 3.10（鎖定）

**Primary Dependencies**: stdlib `imaplib` + `email` · MSAL（OAuth2/XOAUTH2）· `charset-normalizer`。**本期不新增 runtime 相依**（退避重試以 stdlib 實作）。

**Storage**: 工作目錄 `config.json`（新增可選韌性設定）· `token_cache.bin`（MSAL，既有）。

**Testing**: `pytest`，全程離線。以 `tests/imap_sim.py::FakeIMAPConn` 母版模擬器擴充「token 過期 / 連線 EOF / 重連後恢復」情境；保真度仍對拍真 imaplib；雙層驗證（指令日誌 + 狀態快照）。

**Target Platform**: Windows / Linux / macOS 主控台 CLI。

**Project Type**: 單一專案 CLI（src layout）。

**Performance Goals**: 在「中途 token 過期」情境下分類仍 100% 完成、0 重複、0 遺漏、零人工介入；同一分類流程對任一來源夾整夾標頭讀取 = 1 次。

**Constraints**: 全程離線可測；不新增 runtime 相依；`mypy` 乾淨；secrets 永不記錄/外洩；重試/重連**有界**（不無止境、不掛死）；破壞性動作維持 dry-run 預設與冪等。

**Scale/Scope**: 信箱規模達數千～數萬封（超大信箱的分頁/串流不在本期，見 spec Assumptions）。

## Constitution Check

*GATE: 必須於 Phase 0 前通過，Phase 1 設計後再次複查。*

| Principle | 本feature 的遵循方式 | 結論 |
|---|---|---|
| I. Backend Isolation（NON-NEGOTIABLE）| 重連/重試/重新認證**只在 `imap_client.py`**；MSAL 靜默續期在 `auth.py`；client 以**注入 callable** 取得新 token（不 import MSAL/imaplib 到上層）。去重快取以領域型別（uid 集合）跨 seam，非 raw IMAP。`MailBackend` 方法簽名不因此改變（韌性為內部行為）。| ✅ Pass |
| II. OAuth-Only | 續期用 MSAL `acquire_token_silent`（沿用既有授權），無密碼登入；scopes 不變。| ✅ Pass |
| III. Safe-by-Default | 分類維持 dry-run 預設、確認後才搬；重連不繞過確認；續做冪等；move 維持 `UID MOVE` + 安全 fallback（0.5.1）。| ✅ Pass |
| IV. Secrets Never Leak | token 來自 provider、永不記錄；重連/狀態訊息與 `ReauthRequired` 訊息不含任何 token。| ✅ Pass |
| V. Test-First（NON-NEGOTIABLE）| 全部 Red→Green，離線；以擴充後的 `FakeIMAPConn` 模擬 token 過期/EOF/重連恢復。| ✅ Pass |
| VI. Crash-Proof & Honest | 重試/重連**有界**（不掛死、不刷錯 N 次）；續期不可行 → 乾淨停止 + 誠實回報已完成/未完成；發版升版 + 真實日期 CHANGELOG。| ✅ Pass |

**無違規** → Complexity Tracking 留空。

## Project Structure

### Documentation (this feature)

```text
specs/005-bulk-resilience/
├── plan.md              # 本檔
├── research.md          # Phase 0 決策
├── data-model.md        # Phase 1 實體
├── quickstart.md        # Phase 1 驗證指南
├── contracts/           # Phase 1 介面契約（config schema + 後端韌性契約）
└── tasks.md             # /speckit.tasks 產出（非本指令）
```

### Source Code (repository root)

```text
src/mailkeeper/
├── auth.py            # 改：新增「僅靜默續期」取得 token 的路徑；無法靜默時拋明確錯誤（不退化為互動）
├── imap_client.py     # 改：_with_reconnect 包裝（偵測 session 失效/EOF → 注入式 token 續期 → 重建連線 → 重新認證 → 重選夾 → 有界退避重試）；接受 token_provider 與 on_status 回呼
├── classifier.py      # 改：execute 重用 build_report 的權威 uid 快取（不二次整夾掃描）；連續失敗門檻改讀設定；續做仍冪等
├── config_store.py    # 改：解析可選韌性設定（max_consecutive_failures / reconnect/retry 次數），具安全預設
├── config.py          # 改：韌性設定的程式碼層預設值常數
├── cli.py             # 改：_connect 注入 token_provider（auth 靜默續期）與 on_status；分類流程把報告快取傳給 execute；ReauthRequired → 乾淨停止訊息 + 回報數
└── organizer.py       # 改：MailBackend 協定（若新增 on_status 等可選 keyword）保持向後相容

tests/
├── imap_sim.py        # 改：FakeIMAPConn 增「第 N 次操作拋 AccessTokenExpired/EOF、重連後恢復」模式 + token_provider 互動紀錄
├── test_imap_contract.py   # 增：重連/續期/重試的契約（指令日誌驗證 reconnect 序列）
├── test_imap_dataset.py    # 增：token 過期中途 → 分類仍全完成（雙層：日誌 + 快照）
├── test_classifier.py      # 增：execute 重用快取（來源夾整夾讀取 = 1 次）、續做冪等
├── test_config_store.py    # 增：韌性設定解析 + 預設
└── test_cli_*.py           # 增：ReauthRequired → 乾淨停止 + 已完成/未完成回報
```

**Structure Decision**: 沿用既有單一專案 src layout。所有改動落在既有模組，**不新增模組、不新增 runtime 相依**。韌性能力以「後端內部行為 + 注入式回呼」實作，維持 `MailBackend` 介面穩定與 Backend Isolation。

## Complexity Tracking

> 無憲法違規，無需填寫。
