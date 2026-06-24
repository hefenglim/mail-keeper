# Phase 0 Research: Bulk Resilience (R7)

研究目標：把 spec 的 WHAT 轉成可實作的 HOW 決策，並確保不違反憲法（尤其 Backend Isolation）與鎖定技術棧（不新增 runtime 相依）。

---

## R1. 靜默續期：如何在不互動的前提下取得新 token？

- **Decision**: 在 `auth.py` 提供「**僅靜默**」取得 token 的路徑（MSAL `acquire_token_silent`，使用既有 token 快取的 refresh token）。成功 → 回傳新 access token；無法靜默續期（無快取帳號 / refresh token 失效或被撤銷）→ 拋出明確的 `ReauthRequired`（後端中立、訊息不含 secret），**絕不**在此路徑退化為互動式 device flow。
- **Rationale**: 對齊 Clarify Q1=A（續期不可行 → 乾淨停止、不在操作中途彈互動登入）。`acquire_token_silent` 正是 MSAL 為背景續期設計的 API。把「靜默 vs 互動」分離成兩條路徑，讓重連流程只走靜默路徑。
- **Alternatives considered**:
  - 重連時呼叫既有 `get_access_token`（可能退化為 device flow）→ 違反 Q1=A，會在進度途中插入互動，**否決**。
  - 預先延長 token 壽命 → 不在我方控制（由 IdP 決定），**否決**。

## R2. token 如何進到 `OutlookIMAPClient` 而不違反 Backend Isolation？

- **Decision**: `OutlookIMAPClient` 建構時注入一個 **token 提供者 callable**（`Callable[[], str]`）。首次連線與每次重連都呼叫它取得（可能是新的）token。`cli._connect` 提供此 callable，內容為「呼叫 `auth` 的靜默續期」。`imap_client` 不 import MSAL。
- **Rationale**: 憲法 Principle I：MSAL/OAuth 細節不得進 `imap_client`。注入 callable 把「如何取得 token」留在 `auth`/`cli`，`imap_client` 只管「用 token 連線/重連」。與既有注入式回呼（`on_progress`）風格一致、離線可測（測試注入假 provider）。
- **Alternatives considered**:
  - `imap_client` 直接持有 config 並呼叫 `auth` → 把 MSAL 拉進後端，**否決**。
  - 由 `cli` 在外層 catch 後重建整個 client → 上層需辨識 IMAP 連線錯誤（IMAP 細節上浮到 cli），且分類迴圈狀態（present_cache、剩餘項目）難以跨重建保留，**否決**。

## R3. 在哪裡偵測中斷並重連？「透明重連」放後端

- **Decision**: 在 `imap_client.py` 以一個內部包裝 `_with_reconnect(op)` 包住會送出指令的低階操作（`list_headers` 的批次 fetch、`move`、`list_folders` 等）。流程：執行 op → 若擲出「session 失效/連線中斷」類錯誤 → 呼叫 token_provider 靜默續期 → 重建 `IMAP4_SSL` → 重新 XOAUTH2 認證 → （必要時）重新 `select` → **有界退避重試** op。重試/重連次數用盡仍失敗 → 往外拋；`ReauthRequired` 直接往外拋（不重試）。
- **Rationale**: 重連是協定層關注點，放後端讓上層（classifier）保持單純——分類迴圈的「項目級續做」自然成立（每筆 move 在透明重連後即成功，迴圈本就逐項前進）。匯出的「整批重抓」＝`list_headers` 內重連後從頭重跑 SEARCH+FETCH（唯讀、重跑安全）。
- **Alternatives considered**:
  - classifier 主導重連 → IMAP 錯誤辨識上浮、違反隔離，**否決**。
  - 每個 op 各自寫重連 → 重複碼，**否決**（統一用 `_with_reconnect`）。

## R4. 如何辨識「session 失效/連線中斷」vs「單封郵件問題」？

- **Decision**: 視為「需重連」的訊號：`imaplib.IMAP4.abort`、`OSError`/`ssl.SSLError`/socket EOF、以及回應/錯誤訊息含 `AccessTokenExpired` / `Session invalidated` / `AUTHENTICATIONFAILED` 等標記。其餘（如目標夾不存在、單封 `NO`）視為操作層失敗、**不**觸發重連（維持既有 per-item 處理與 0.5.1 的 move 安全 fallback）。
- **Rationale**: 精準分流避免「資料問題被當連線問題狂重連」或反之。標記字串比對涵蓋 Outlook 實測訊息（見 `doc/lessons-learned.md` 事故 log）。
- **Alternatives considered**: 只靠例外型別 → Outlook 把 token 過期包成一般錯誤訊息，型別不足以分流，故**併用訊息標記**。

## R5. 退避重試策略（不新增相依）

- **Decision**: 有界**指數退避**（如 0.5s, 1s, 2s…上限封頂），最大重試/重連次數可設定，用 stdlib `time.sleep` 實作。暫時性失敗在次數內被吸收；用盡才計入放棄並停止。
- **Rationale**: 簡單、無新相依（憲法鎖定棧）、足以吸收抖動。封頂避免長時間掛死（Principle VI）。
- **Alternatives considered**: 引入 `tenacity`/`backoff` 套件 → 新 runtime 相依需修憲，**否決**。

## R6. 消除重複整夾讀取（US2，依 Q2=A）

- **Decision**: `build_report` 既有的 `uid_cache`（每來源夾整夾標頭只抓一次）成為**權威快取**；`classify` 把它傳給 `classifier.execute`，execute 以此為 `present` 起始集合、**不再二次整夾掃描**，搬走即 `discard` 更新。確認後到執行前消失的郵件由 `move` 動作安全失敗回報（既有 stale-uid 處理）。`list_folders` 在一次分類流程內也只取一次並共用。
- **Rationale**: 對齊 Q2=A：報告讀的為權威、執行重用、TOCTOU 由 move 動作兜。把「整夾讀取」從 2 次降到 1 次（SC-003）。
- **Alternatives considered**: 把讀取延到 execute（Q2 的 B）→ 報告無法事先標不存在的郵件，使用者體驗較差，**否決**（Clarify 已定 A）。

## R7. 韌性設定的承載與預設

- **Decision**: 韌性門檻放既有 `config.json`（非機密），鍵如 `max_consecutive_failures`、`max_reconnect_attempts`、`max_retries_per_op`；缺漏/無效 → 用 `config.py` 的安全程式碼預設。`_MAX_CONSECUTIVE_FAILURES`（現寫死於 classifier）改讀設定。
- **Rationale**: 對齊 FR-008；沿用 feature 002 的設定外部化機制；安全預設確保不崩潰。
- **Alternatives considered**: 環境變數 / CLI flag → 與既有設定機制不一致，**否決**（統一走 config.json）。

## R8. 恢復期間的可見性（FR-009）

- **Decision**: `OutlookIMAPClient` 接受可選 `on_status: Callable[[str], None]` 後端中立回呼；重連/續期/重試時發出狀態（如「連線中斷，重新連線中…」「已重新連線，繼續處理」）。`cli` 把它接到 `console`/`progress`（編碼安全 stderr）。`imap_client` 不 import `console`。
- **Rationale**: 對齊 FR-009 與 Principle I（後端不直接碰輸出層，用注入回呼）。與 `on_progress` 同風格。
- **Alternatives considered**: 後端直接 print → 耦合輸出層、違反隔離，**否決**。

---

## 測試策略（離線、對拍、雙層）

- 擴充 `FakeIMAPConn`：可設定「第 N 次某類操作擲 `AccessTokenExpired`/EOF，呼叫 token_provider 重連後恢復」；記錄 token_provider 被呼叫次數與重連序列於指令日誌。
- 保真度：重連涉及的 `authenticate`/`select` 回應沿用已對拍真 imaplib 的格式（不新增未驗證回應結構；若新增則先加 fidelity case）。
- 雙層驗證：第一層查指令日誌（偵測→續期→重認證→重選→重試的序列正確且有界）；第二層查 `snapshot()`（最終全部搬完、0 重複、0 遺漏；他人 `\Deleted` 不受波及）。
