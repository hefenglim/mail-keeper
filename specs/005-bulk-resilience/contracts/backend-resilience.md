# Contract: 後端韌性 seam（internal）

定義 R7 跨 `cli`/`auth`/`imap_client` 的內部契約。**`MailBackend` 協定的既有方法簽名不變**（韌性為後端內部行為 + 注入回呼），確保 Backend Isolation 與向後相容。

## 1. Token 提供者（注入到 `OutlookIMAPClient`）

```
TokenProvider = Callable[[], str]
```

- 由 `cli._connect` 提供；內容為呼叫 `auth` 的**僅靜默**續期取得（可能更新的）access token。
- `OutlookIMAPClient` 於首次連線與**每次重連**呼叫之；自身不 import MSAL（Principle I）。
- 提供者若無法靜默續期 → 擲 `ReauthRequired`（見下）。

## 2. 僅靜默續期（`auth.py`）

- 新增「僅靜默」取得 token 的函式：成功回傳 token；無法靜默續期（無快取帳號 / refresh token 失效或撤銷）→ 擲 **`ReauthRequired`**。
- **MUST NOT** 在此路徑退化為互動 device flow（Clarify Q1=A）。
- 既有 `get_access_token`（可含互動）維持不變，供首次登入使用。

## 3. 重連行為（`imap_client.py` 內部）

- 內部 `_with_reconnect(op)` 包住會送指令的低階操作。偵測「session 失效/連線中斷」（`imaplib.IMAP4.abort`、socket/SSL EOF、訊息含 `AccessTokenExpired`/`Session invalidated`/`AUTHENTICATIONFAILED`）時：
  1. 呼叫 `TokenProvider` 靜默續期；
  2. 重建 `IMAP4_SSL` + XOAUTH2 重新認證；
  3. 必要時重新 `select` 還原選取夾；
  4. **有界退避重試** op（次數/退避由韌性設定）。
- 用盡重連/重試 → 往外拋（讓上層乾淨收場）。`ReauthRequired` **不重試**、直接往外拋。
- 非連線類失敗（單封 `NO`、目標夾不存在等）**不**觸發重連，維持既有 per-item 處理與 0.5.1 的 move 安全 fallback。

## 4. 狀態回呼（注入到 `OutlookIMAPClient`，可選）

```
on_status: Callable[[str], None] | None = None
```

- 重連/續期/重試時發出後端中立狀態字串（如「連線中斷，重新連線中…」）。`cli` 接到編碼安全的 stderr 輸出；`imap_client` 不 import `console`（Principle I）。
- 訊息**不得**含任何 token/secret（Principle IV）。

## 5. `ReauthRequired`（後端中立錯誤）

- 表示「需使用者重新登入」的終結訊號（非暫時性、不重試）。
- 由 `cli` 錯誤邊界轉為**乾淨停止**：印明確需重新登入訊息 + 回報已完成/未完成數量（Clarify Q1=A、FR-004）。訊息不含 secret。

## 6. 分類流程（`cli` ↔ `classifier`）

- `classify` 把 `build_report` 的**權威 uid 快取**傳入 `classifier.execute`（`execute` 不再二次整夾掃描；FR-007）。
- `execute` 的連續失敗門檻改讀韌性設定（FR-008）。
- 介面相容：新增參數皆為可選 keyword，預設行為與現狀一致。

## 驗收（離線、對拍、雙層）

- **重連序列**（指令日誌）：操作中途注入 `AccessTokenExpired` → 日誌出現 `authenticate`（續期重認證）+ 重新 `select` + 重試該操作，且次數有界。
- **全完成**（狀態快照）：token 中途過期情境下分類最終 100% 完成、0 重複、0 遺漏；他人 `\Deleted` 不受波及。
- **乾淨停止**：`TokenProvider` 擲 `ReauthRequired` → 操作停止、cli 印需重新登入 + 正確已完成/未完成數、退出碼非零、無 traceback、無 secret。
- **單次整夾讀取**：一次分類流程對任一來源夾整夾標頭讀取 = 1 次。
