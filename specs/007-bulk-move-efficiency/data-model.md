# Phase 1 — Data Model: Bulk Move Efficiency & Idempotency (Phase 2)

本期不引入新的持久化資料；以下為跨層流動的領域概念與其約束。

## 既有型別（沿用 / 微調）

- **`ClassificationRow`**（`csv_io`）：工作表一列。輸入不變。
- **`ReportItem`**（`classifier`）：每列判定。不變。
- **`MoveResult`**（`classifier`）：`row, ok, error`。結果集合不變（FR-008）；**呈現順序**依 CSV 列序（FR-002）。
- **`ClassifyCache`**：沿用（存在性 UID 集合，006）。

## 新概念

### Move Group（搬移群組）
- **鍵**: `(current_folder, target_folder)`。
- **值**: 該群候選的 uid 清單（依候選原索引保序）。
- **用途**: 分組處理單位；每群再依固定上限分塊（D3）為一或多個批次。
- **不變式**: 分組為穩定排序（同輸入決定性；FR-001、SC-003）；不改變結果集合（FR-008）。

### Batch Move Outcome（批次搬移結果）
- **型別**: `dict[str, str | None]`（uid → `None`=成功 / 錯誤訊息）。為 `MailBackend.move_many` 的回傳。
- **語意**: 批次內每封獨立成敗；批次層失敗 → 退逐封後仍以此 dict 逐封歸因（FR-005、SC-004）。
- **映射**: `execute` 將 dict 依候選原索引映回 `MoveResult`，最終以 CSV 列序輸出。

### Selected Mailbox State（目前選取狀態）
- **型別**: `tuple[mailbox: str, readonly: bool] | None`（client 內部狀態）。
- **用途**: `_ensure_selected` 免重複 SELECT（FR-003、SC-001）。
- **生命週期**: `connect()`/`_reconnect()` 重置為 `None`（連線換了、選取失效）。

### Move Idempotency State（後備搬移冪等判定）
- **輸入**: 來源夾中該 uid 的存在與 `\Deleted` 旗標（重試前查）。
- **判定**: 不在→已完成（成功）；在且 `\Deleted`→已 copy（跳 COPY、只 expunge）；在且未標→正常 copy（FR-006、SC-005）。

## 狀態流（execute 一次搬移）

```text
cands = candidates(items)                      # CSV 序
groups = stable_group_by(cands, key=(current,target))   # 決定性分組（保留原索引）
for group in groups:
    for chunk in chunked(group.uids, MOVE_BATCH_MAX):    # D3 上限分塊
        outcome = backend.move_many(chunk, target, current)   # 批次；內部失敗退逐封
        result[原索引] = MoveResult(...)                       # 依 outcome 映回
        on_progress(done += len(chunk), total)               # 每批推進
    # 連線層級失敗（move_many 拋出 session-lost/ReauthRequired）→ 往外傳、execute 停止
return [result[i] for i in CSV 序]             # 還原 CSV 列序
```

> 早停僅由連線層級失敗（重連用盡）觸發；單列資料失敗只記 `MoveResult(ok=False)`、不早停、不連坐（FR-013、SC-010）。
