# Contract — `list_headers` resumable read (P5/P7)

## Behavior
- 讀取指定資料夾所有郵件標頭，分批 `UID SEARCH ALL` + `UID FETCH (UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])`，回 `list[MailHeader]`（UID 全非空）。
- **可續傳（P5）**：連線中斷/重連後從中斷處續抓——重連後重新 `SELECT`(readonly)+`SEARCH ALL`，與已取得 UID 取差集、**只抓差集**；不重抓已取得批次。
- **UIDVALIDITY 變更**：重連後該夾 UIDVALIDITY 變更 → 捨棄已取得進度、整批重抓（不沿用過時 UID）。
- **有界**：沿用 `max_reconnect_attempts`；每成功一批重置失敗計數（多次中斷皆可續）；逾上限 → 外拋（不靜默產出不完整）。
- **進度**：`on_progress(done, total)`，`done` 跨重連延續（不歸零）、`total`=現存郵件數。
- **解析（P7）**：標頭以 header-only 解析；`MailHeader` 各欄位（含 `_decode` 解碼）逐字等價現況。

## 不變式 / 引擎斷言
| 斷言 | 期望 |
|---|---|
| 中途 `arm_expiry(before_op="fetch")` + 重連 | `command_counts["UID FETCH"]` ≈ ⌈N/批⌉（不翻倍）；`redundant_full_folder_reads=={}` |
| 最終結果 | headers 數=N、UID 全非空、無重複、無遺漏；`assert_all_fetches_request_uid()` |
| `set_uidvalidity` 變更 + 斷線 | 重連後重抓、結果正確（不沿用過時 UID） |
| 多編碼主旨 | 主旨/寄件者等解碼逐字等價現況 |

## 後端中立
- 續傳/解析細節僅在 `imap_client.py`；回傳領域型別 `MailHeader`；上層零改動（FR-008）。
