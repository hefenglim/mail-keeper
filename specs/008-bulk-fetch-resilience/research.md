# Phase 0 — Research & Decisions: Bulk Fetch Resilience & Tuning (Phase 3)

無 `NEEDS CLARIFICATION`（spec 經 `/speckit.clarify` 已釘三項）。實作取向決策：

## D1 — `list_headers` 可續傳韌性迴圈（P5）

- **Decision**: 不再用 `self._with_reconnect(lambda: self._list_headers_impl(...))` 包整體。改為 `list_headers` 自帶迴圈：維持 `collected: dict[str, MailHeader]`（已取得，uid→header）與上次 `uidvalidity`；每輪 `_ensure_selected(folder, readonly=True)` → 讀目前 UIDVALIDITY（`_current_uidvalidity()`，取 imaplib `untagged_responses['UIDVALIDITY']`）→ 若與上次不同則 `collected.clear()`（UIDVALIDITY 變更重抓）→ `UID SEARCH ALL` 取現存 uid 清單 → `remaining = [u for u in all_uids if u not in collected]` → 分批 FETCH `remaining`，每封存入 `collected`、`on_progress(len(collected), total)`。全成功 → 回 `[collected[u] for u in all_uids]`（SEARCH 序）。
- **重連/有界**：迴圈 `try/except`：`ReauthRequired` 外拋；session-lost 且未逾 `max_reconnect_attempts` → status + 退避 + `_reconnect()` + 續迴圈（`collected` 保留 → 只抓差集）；**每成功一批即 reset 失敗計數**（多次中斷皆可續，沿用有界精神）；逾上限 → 外拋（不靜默產出不完整）。
- **Rationale**: 以 UID 集合差集續抓（UID 為穩定鍵），對亂序/gap 穩健；UIDVALIDITY 變更安全重抓；進度跨重連延續（`len(collected)` 單調遞增）。Clarify 已確認。
- **Alternatives rejected**: 以「最大已抓 UID 為游標」續抓（假設 UID 遞增、遇 gap/亂序易錯）；維持 `_with_reconnect` 整體包裝（即現況整批重抓）。

## D2 — FETCH 批量可調（P6）

- **Decision**: `config.py` 加 `FETCH_BATCH_DEFAULT = 50`；`config_store.Configuration` 加 `fetch_batch_size: int`，以 `_as_positive_int(data.get("fetch_batch_size"), FETCH_BATCH_DEFAULT)` 解析（無效/缺漏/非正 → 預設；下限 1 由 `_as_positive_int` 的 `>0` 保證）。`OutlookIMAPClient.__init__(..., fetch_batch_size: int = config.FETCH_BATCH_DEFAULT)` → `self._fetch_batch = max(1, fetch_batch_size)`；`list_headers` 用 `self._fetch_batch`。`cli` 建構 client 時注入 `cfg.fetch_batch_size`。
- **Rationale**: 完全比照 feature 005 韌性設定的解析/注入路徑（後端中立、無效退預設、不崩潰）。
- **Alternatives rejected**: 環境變數或 CLI 旗標（與既有 config.json 慣例不一致）。

## D3 — 標頭解析微優化（P7）

- **Decision**: FETCH 標頭 literal 的解析由 `email.message_from_bytes(part[1])` 改為 `email.parser.BytesHeaderParser(policy=email.policy.compat32).parsebytes(part[1])`（只解析表頭、不建構 body 結構）。`msg.get("Subject"/"From"/"To"/"Date")` 與既有相同 → `_decode` 路徑、輸出逐字不變。
- **Rationale**: HEADER.FIELDS literal 本就只有表頭 + 空行；header-only 解析省去 body 解析機制，超大信箱邊際省 CPU；行為等價。
- **Alternatives rejected**: 自寫表頭切割（重造輪子、易錯）；維持整封解析（無謂開銷）。
- **驗證**: 母版多編碼主旨（ASCII/CJK/emoji/encoded-word/折行）逐字比對；`_decode_mutf7` 為效能指標誤報、不動（report P7 已註）。

## D4 — 驗證取向（測請求端 + log）

- **續傳不整批重抓**：`arm_expiry(before_op="fetch", nth=k, mode="eof")` + `token_provider`，讀 N 封 → `command_count("UID FETCH")` ≤ ⌈N/批⌉ + 1（**不翻倍**；+1 容許失敗那批重試一次）、最終 headers 數=N、UID 全非空、無重複、`assert_all_fetches_request_uid()`。註：不用 `redundant_full_folder_reads==​{}`——引擎將「任一夾 >1 次 FETCH」即視為冗餘，對正常多批讀取恆為真，非「續傳重抓」的有效指標。
- **UIDVALIDITY 變更**：讀到一半 `set_uidvalidity(folder, new)` + 注入斷線 → 重連後偵測變更 → 重抓 → 結果正確（不沿用過時 UID）。
- **批量**：`config_store` 單元測試（預設/無效/下限）；引擎驗 `fetch_batch_size=M` → FETCH 次數=⌈N/M⌉。
- **解析等價**：`list_headers` 對母版 → 主旨/寄件者等與優化前逐字一致。
- 引擎已具 `arm_expiry`/`set_uidvalidity`/log 分析，**無需擴充引擎**。
