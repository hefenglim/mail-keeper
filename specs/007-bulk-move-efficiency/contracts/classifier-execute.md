# Contract — Classifier: grouped batch execute

## `execute` — 分組 + 批次 + 連線層級早停

- 取 `candidates(items)`（CSV 列序）→ 依 `(current_folder, target_folder)` **穩定分組**（保留各候選原索引；FR-001、決定性 SC-003）。
- 逐群處理：目標夾不存在則 `ensure_folder` 一次；以共用 `ClassifyCache.source_uids` 確認來源存在（沿用 006，不重查）；對該群 uid（依固定上限分塊）呼叫 `backend.move_many(chunk, target, current)`。
- 將 `move_many` 回傳 dict 依 uid 映回各候選之 `MoveResult`；最終回傳 `list[MoveResult]` 依**原 CSV 列序**（FR-002、SC-003）。
- **進度**：`on_progress(done, total)` 每批完成 `done += 該批封數`，推進至 `total`（FR-009、SC-009）。
- **早停（連線層級）**：單列資料失敗（`move_many` dict 中該 uid 有錯）→ 記 `MoveResult(ok=False)`、**繼續**處理其餘（不早停、不連坐；FR-013、SC-010）。連線層級失敗（`move_many` 因重連用盡而拋 session-lost）→ 往外傳，`execute` 停止並回傳已處理結果；`ReauthRequired` 仍往外傳由 cli 乾淨停止。
- 移除原「連續資料失敗計數早停」（`consecutive_failures` / `max_consecutive_failures` 對 classify 不再驅動早停）。

### 不變式
- 結果集合（被搬郵件與目標夾、每列成敗）與優化前 100% 等價（FR-008、SC-003）。
- 批次部分失敗 → 逐封歸因，與逐封處理等價（FR-005、SC-004）。
- dry-run 預設不變（未確認不呼叫 execute 的真實搬移；FR-010、SC-008）。

## CLI 呈現
- 逐列成功/失敗清單依 `execute` 回傳順序 = CSV 列序。
- 連線層級停止時，回報已完成/未完成數（沿用 006/005 的乾淨停止文案，配合連線層級語意微調）。

## 後端中立
- `classifier`/`cli` 僅透過 `MailBackend.move_many`/`move` 參與，不 import imaplib、不特例化後端（Principle I、FR-011）。
