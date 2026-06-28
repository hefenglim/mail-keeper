# Phase 1 — Data Model: Bulk Fetch Resilience & Tuning (Phase 3)

本期不引入新的持久化資料；以下為讀取流程的概念與設定。

## 既有型別（不變）

- **`MailHeader`**：`uid, subject, sender, date, recipients`。輸出**逐字不變**（P7 只換解析器）。
- **`Configuration`**（`config_store`）：新增一欄 `fetch_batch_size`（見下）。其餘不變。

## 新概念

### Fetch Progress（讀取進度，`list_headers` 內部）
- `collected: dict[str, MailHeader]`：本次讀取「已取得標頭」的 uid→header；跨重連保留。
- `all_uids: list[str]`：目前該夾現存 UID（每輪 `SEARCH ALL` 取得）；`remaining = [u for u in all_uids if u not in collected]`。
- `uidvalidity`：上次見到的 UIDVALIDITY；變更 → `collected.clear()` 整批重抓。
- **不變式**：`len(collected)` 單調遞增（進度延續）；最終回傳 `[collected[u] for u in all_uids]`（SEARCH 序）、UID 全非空、無重複、無遺漏。

### Fetch Batch Size（每批讀取封數設定）
- 來源：`config.json` 可選鍵 `fetch_batch_size`。
- 解析：`_as_positive_int(value, FETCH_BATCH_DEFAULT)`（預設 **50**、下限 **1**；無效/缺漏/非正 → 預設）。
- 流動：`Configuration.fetch_batch_size` → cli → `OutlookIMAPClient(fetch_batch_size=...)` → `self._fetch_batch` → `list_headers` 分批依據（FR-004）。

## 狀態流（list_headers 一次讀取，含重連）

```text
collected = {}; uidvalidity = None; failures = 0
loop:
  try:
    _ensure_selected(folder, readonly=True)
    v = _current_uidvalidity()
    if uidvalidity is not None and v != uidvalidity: collected.clear()   # UIDVALIDITY 變更重抓
    uidvalidity = v
    all_uids = UID SEARCH ALL
    for batch in chunked([u for u in all_uids if u not in collected], self._fetch_batch):
        FETCH batch → collected[uid] = MailHeader(...)（BytesHeaderParser）
        on_progress(len(collected), len(all_uids)); failures = 0   # 進度延續、成功即重置失敗計數
    return [collected[u] for u in all_uids]
  except ReauthRequired: raise
  except session-lost as exc:
    if failures >= max_reconnect_attempts: raise   # 有界、不靜默產出不完整
    failures += 1; status; backoff; _reconnect()    # collected 保留 → 只抓差集
```
