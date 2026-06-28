# Contract — configurable FETCH batch size (P6)

## config.json
- 可選鍵 `fetch_batch_size`：每批 `UID FETCH` 的封數。
- 預設 **50**（`config.FETCH_BATCH_DEFAULT`）、下限 **1**。
- 無效（非整數/型別錯誤/≤0）或缺漏 → 退回預設、**永不崩潰**（比照 feature 005 韌性設定，`_as_positive_int`）。

## 流動
`config.json` → `config_store.Configuration.fetch_batch_size` → `cli` 建構時注入 → `OutlookIMAPClient(fetch_batch_size=...)` → `self._fetch_batch = max(1, fetch_batch_size)` → `list_headers` 分批依據。

## 不變式 / 斷言
| 斷言 | 期望 |
|---|---|
| `config_store.load` 缺 `fetch_batch_size` | = 50（預設） |
| 值為 `"abc"` / `0` / `-3` / `null` | 退回 50（不崩潰） |
| 值為 M（正整數） | `Configuration.fetch_batch_size == M` |
| 引擎讀 N 封、`fetch_batch_size=M` | `command_counts["UID FETCH"] == ⌈N/M⌉` |

## 後端中立
- 上層只傳 int（領域中立）；批量如何用於 `UID FETCH` 屬 `imap_client` 細節（FR-008）。
