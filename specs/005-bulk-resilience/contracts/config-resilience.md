# Contract: config.json 韌性設定（user-facing）

R7 在既有工作目錄 `config.json` 新增**可選**的韌性設定。皆可省略；省略或無效時以程式碼安全預設運作，不崩潰。

## 新增鍵（皆可選）

```jsonc
{
  // ── 既有（feature 002）──
  "client_id": "…",
  "email": "…@outlook.com",
  // imap_host / imap_port / timeout（可選，既有）

  // ── R7 新增（皆可選，缺漏用安全預設）──
  "max_consecutive_failures": 3,    // 連續「真正失敗」達此數 → 停止整體操作
  "max_reconnect_attempts": 3,      // 單一中斷事件最多重連次數
  "max_retries_per_op": 2,          // 單一操作重連後最多重試次數
  "backoff_base_seconds": 0.5,      // 指數退避起點秒數
  "backoff_cap_seconds": 8          // 指數退避封頂上限秒數（不超過此值）
}
```

## 規則

- **型別/範圍**：整數類須為正整數；`backoff_base_seconds`、`backoff_cap_seconds` 須為正數（且 `backoff_cap_seconds` ≥ `backoff_base_seconds`）。任一無效（非數、≤0、型別錯）→ 該鍵退回程式碼預設值；其餘有效鍵照用。**絕不**因設定錯誤崩潰（Principle VI、FR-008）。
- **機密**：此檔仍 gitignored；**絕不**寫入 token/access token（Principle IV，既有規則不變）。
- **相容**：舊 `config.json`（無這些鍵）行為等同採用預設值；無破壞性變更。

## 驗收

- 給定缺這些鍵的 `config.json` → 以預設值運作。
- 給定 `max_consecutive_failures: "x"`（無效）→ 退回預設、不崩潰、其餘鍵仍生效。
- 給定 `max_consecutive_failures: 5` → 連續失敗門檻變為 5（行為隨設定改變，SC 對映 US3 場景2）。
