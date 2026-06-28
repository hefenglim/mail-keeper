# Phase 0 — Research & Decisions: Bulk Move Efficiency & Idempotency (Phase 2)

無 `NEEDS CLARIFICATION`（spec 經 `/speckit.clarify` 已釘早停／批次大小／進度）。以下為實作取向決策。

## D1 — 免重複 SELECT：`_ensure_selected((mailbox, readonly))`（P3 / C2）

- **Decision**: `OutlookIMAPClient` 加狀態 `self._selected: tuple[str,bool] | None`；`_ensure_selected(mailbox, readonly)` 僅在 `self._selected != (mailbox, readonly)` 時才 `self._conn.select(mailbox, readonly=readonly)` 並更新狀態。`connect()` 與 `_reconnect()` 設 `self._selected = None`（連線換了、選取失效）。`_move_impl`/`mark_read`/`flag`（及 `_move_many_impl`）改走 `_ensure_selected`。
- **Rationale**: 同夾連續操作不重選 → 往返砍半；重連必重置確保正確性。
- **Alternatives rejected**: 每次都 select（現況浪費）；在 classifier 層快取（洩漏協定、違 Principle I）。
- **驗證**: `server.redundant_selects() == 0`（同夾同模式）；重連後 snapshot/log 顯示重新 SELECT。

## D2 — 批次搬移：後端中立 `move_many`（P2）

- **Decision**: `MailBackend` 加 `move_many(uids: list[str], dest_folder: str, mailbox: str = "INBOX") -> dict[str, str | None]`（uid → `None`=成功 / 錯誤訊息=失敗）。`OutlookIMAPClient._move_many_impl`：`_ensure_selected(mailbox, rw)` → 以 `UID MOVE <uid-set>` 批次（分塊見 D3）；批次回 OK＝全成功；批次非 OK／伺服器不支援 → 對該塊**退回逐封 `move`** 以精確歸因。整個 `move_many` 以 `_with_reconnect` 包裝。
- **Rationale**: 跨 seam 只傳 uid 與結果 dict（領域型別）；`move`（單封）保留給 `organizer.run`；classifier 改用 `move_many`。批次失敗退逐封滿足「部分失敗逐封歸因、不連坐」（FR-005）。
- **Alternatives rejected**: 改 `move` 簽名收 list（破壞 organizer 既有呼叫）；classifier 直接組 UID 集合（洩漏協定）。

## D3 — 批次大小：固定上限分塊（Clarify）

- **Decision**: 以「整個 (來源,目標) 群為一批」，超過固定常數上限（`config.MOVE_BATCH_MAX`，預設如 200）則分塊為多批。**程式內固定、不開放設定**（可調批量屬延後的 P6）。
- **Rationale**: 限制命令列長度與「批次失敗退逐封」的爆炸半徑；200 為保守安全值。
- **Alternatives rejected**: 不設上限（超大群命令列過長、退逐封範圍大）；開放 config（與 P6 重疊、超範圍）。

## D4 — 後備搬移冪等（C1）

- **Decision**: `_move_impl` 後備路徑（無 `UID MOVE`）改為：重試前先以該 UID 之 `UID SEARCH`／flags 判斷狀態——
  1. **UID 已不在來源** → 前次已搬走，視為成功返回（no-op）。
  2. **UID 仍在且已標 `\Deleted`** → 前次已 COPY（標刪在 COPY 後），**跳過 COPY**，只補 `UID EXPUNGE`（無則整夾 EXPUNGE 兜，但仍 UID 限定優先）。
  3. **UID 仍在且未標 `\Deleted`** → 正常 COPY → 標刪 → UID EXPUNGE。
- **Rationale**: 消除「COPY 成功後、EXPUNGE 前斷線重試 → 重做 COPY → 重複複本」。以 UID 狀態（穩定識別）判前次進度，毋需脆弱的 Message-ID 比對。
- **Alternatives rejected**: 目標夾以 Message-ID 去重（需抓內容、跨夾比對，脆弱且昂貴）。
- **驗證**: `arm_expiry(before_op="expunge"/"store", ...)` 於 COPY 後注入中斷 → 重連重試 → snapshot 目標夾複本數正好 1；feature 006 的 C1 xfail 測試自動 xpass、移除 marker。

## D5 — 早停改連線層級（Clarify）

- **Decision**: `classifier.execute` 不再以「連續資料失敗計數」提前停止。單列資料失敗（`move_many` 回傳 dict 中該 uid 有錯）記為失敗 `MoveResult`、繼續處理其餘。**連線層級失敗**（`_with_reconnect` 重連用盡後拋出的 session-lost）往外傳 → execute 停止並回傳已處理結果；`ReauthRequired` 仍往外傳由 cli 乾淨停止。
- **Rationale**: 分組／批次改變列序 → 連續計數失去意義且會改變被處理集合（006 F1）。連線層級判定與順序無關、更正確（真死連線仍由重連用盡偵測而停）。
- **影響/遷移**: 移除 `execute` 的 `consecutive_failures`/`max_consecutive_failures` 早停邏輯；既有 `test_execute_aborts_after_consecutive_failures`/`test_execute_threshold_configurable` 需遷移（資料失敗不再早停；連線層級失敗以重連用盡 → 拋出 → 停）。cli 的「剩餘 N 筆」回報配合（連線層級停止時回報已完成/未完成）。`config.MAX_CONSECUTIVE_FAILURES` 對 classify 不再驅動早停（保留常數以免破壞既有 import；標註不再用於資料失敗）。

## D6 — 候選分組 + 輸出還原 CSV 列序 + 批次進度

- **Decision**: `execute` 取 `candidates(items)`（CSV 序）→ 依 `(current_folder, target_folder)` 穩定分組（保留各候選原索引）→ 逐群（每群再依 D3 分塊）呼叫 `move_many` → 將每 uid 結果映回原索引 → 回傳 `MoveResult` 依 **CSV 列序**。進度 `on_progress(done, total)` 每批完成 `done += 該批封數`，推進至 `total`。
- **Rationale**: FR-001/002/004/009；穩定 `sorted` 保決定性；沿用 006 的「輸出還原 CSV 序」決策。

## D7 — 驗證取向（測請求端 + log + snapshot）

- 引擎雙層：log（`redundant_selects()==0`、`command_counts["UID MOVE"]` = ⌈N/批⌉、`bottleneck()`）+ `snapshot()`（資料正確、他人 `\Deleted` 不被波及）。
- 異常：`arm_expiry` 注入「搬移中途／COPY 後」中斷驗重連續完與後備冪等；注入單列失敗 vs 連線中斷分別驗 D5 早停語意。
- 分類層：`FakeBackend.move_many`（含 UID 狀態）驗分組順序、結果 CSV 序、部分失敗歸因、早停。
