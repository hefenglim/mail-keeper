# Phase 1 Data Model: Bulk Resilience (R7)

本期不引入持久化資料庫；以下為執行期的概念實體與其狀態轉移，對映 spec 的 Key Entities 與 FR。

---

## Entity: 韌性設定（Resilience Settings）

使用者可調整的門檻集合，具安全程式碼預設值。

| 欄位 | 意義 | 預設（程式碼層） | 來源 |
|---|---|---|---|
| `max_consecutive_failures` | 連續「真正失敗」達此數即停止整體操作 | 3（沿用現值） | `config.json`（可選），缺漏用 `config.py` 預設 |
| `max_reconnect_attempts` | 單一中斷事件最多重連次數 | 例：3 | 同上 |
| `max_retries_per_op` | 單一操作（如一筆 move）重連後最多重試次數 | 例：2 | 同上 |
| `backoff_base_seconds` | 退避基準秒數（指數退避起點） | 例：0.5 | 同上 |
| `backoff_cap_seconds` | 退避封頂上限秒數（指數退避不超過此值，使「上限封頂」可測） | 例：8 | 同上 |

**驗證規則**：值缺漏/型別錯/非正數 → 退回安全預設、不崩潰（FR-008、Principle VI）。
**對映**：FR-005、FR-006、FR-008。

---

## Entity: 連線工作階段（Session）

與信箱的連線及其有效性；可在不需互動登入的前提下重建。

**狀態**：`disconnected` → `connected` →（中斷）→ `reconnecting` →（靜默續期成功）→ `connected` ／（續期不可行）→ `reauth_required(terminal)`。

| 屬性 | 意義 |
|---|---|
| token 提供者 | 注入的 `Callable[[], str]`，每次（重）連取得（可能更新的）token |
| 目前選取夾 | 重連後需重新 `select` 以還原狀態 |
| 重連計數 | 對照 `max_reconnect_attempts`，超過即放棄 |

**轉移觸發**：偵測 session 失效/連線 EOF（research R4 的標記）→ `reconnecting`。
**對映**：FR-001、FR-002、FR-004（`reauth_required` 為乾淨停止訊號）、FR-006。

---

## Entity: 操作工作清單（Work List）

一次操作中所有待處理項目及其狀態，支援「從斷點續做」與「不重複、不遺漏」。

| 項目狀態 | 意義 |
|---|---|
| `pending` | 尚未處理 |
| `done` | 已成功（分類：已搬移） |
| `skipped/infeasible` | 不適用（如目標==來源、缺 uid） |
| `gone` | 執行時來源 uid 已不存在（TOCTOU，安全失敗、不重搬） |
| `failed` | 嘗試後失敗（非連線類；或重連用盡） |

**冪等保證**：續做只處理仍 `pending` 者；重跑同一份工作表時，已 `done`（來源已無該 uid）自然落入 `gone`/`infeasible`，不重搬（FR-003、FR-010）。
**分類細粒度**：項目級（逐筆）。**匯出細粒度**：整批（唯讀，重連後整批重抓；其「工作清單」即整份標頭）。
**對映**：FR-003、FR-010、SC-001、SC-005。

---

## Entity: 來源夾權威 UID 快取（Authoritative Source-UID Cache）

同一分類流程中，每個來源資料夾的現存 UID 集合，**於檢查報告階段讀取一次**，執行階段重用。

| 屬性 | 意義 |
|---|---|
| `folder -> set[uid]` | 各來源夾整夾現存 UID（報告階段所讀，權威） |
| 更新方式 | 執行階段每成功搬走一封即 `discard`；**不重讀整夾** |

**規則**：執行時 `row.uid not in cache[folder]` → 標 `gone`；TOCTOU（確認後到執行前消失）由 `move` 動作安全失敗回報。整夾讀取次數 = 1（SC-003）。
**對映**：FR-007、SC-003。

---

## 關係圖（概念）

```
Resilience Settings ──tunes──▶ Session（重連/重試上限、退避）
                     └──tunes──▶ Work List（連續失敗上限 → 何時停止）
Session ──recovers──▶ 讓 Work List 的 pending 項目得以續做
Authoritative Source-UID Cache ──feeds──▶ Work List 的可行性判斷（執行重用、不重讀）
```
