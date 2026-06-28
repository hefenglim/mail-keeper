# Contract — Backend: `move_many`, `_ensure_selected`, idempotent fallback

## `MailBackend.move_many`（新增，後端中立）

```python
def move_many(
    self, uids: list[str], dest_folder: str, mailbox: str = "INBOX"
) -> dict[str, str | None]: ...
```

- 回傳 `dict[uid, None | error]`：`None`=該封搬移成功；字串=失敗原因。
- 對 `uids` 中的每一封：成功者搬入 `dest_folder` 並自 `mailbox` 移除；失敗者如實記錄、**不連坐**其他封（FR-005）。
- 目標夾不存在時的處理沿用既有單封 `move` 的語意（呼叫端 `execute` 仍負責 `ensure_folder`）。
- 連線中斷／權杖過期 → 透明重連並完成（`_with_reconnect` 包裝）；重連用盡之 session-lost／`ReauthRequired` **往外拋**（連線層級失敗，FR-007/FR-013）。
- 單封 `move(uid, dest, mailbox)` 仍保留（`organizer.run` 用），語意不變。

### IMAP 實作約束（`imap_client`，不外洩）
- `_ensure_selected(mailbox, readonly=False)` → 以 `UID MOVE <uid-set>` 批次；`uids` 超過 `config.MOVE_BATCH_MAX` 分塊。
- 批次回 `OK` → 該塊全成功；批次非 `OK`（或伺服器不支援 MOVE）→ 對該塊**退回逐封 `move`** 以精確歸因。
- IMAP 細節僅存於此檔（Principle I）。

## `_ensure_selected`（P3 / C2）

- 追蹤 `self._selected: (mailbox, readonly) | None`；僅在「未選／夾不同／模式不同」時 `select`。
- `connect()` / `_reconnect()` MUST 重置 `self._selected = None`。
- `_move_impl` / `_move_many_impl` / `mark_read` / `flag` 改走 `_ensure_selected`。

### 引擎斷言
| 斷言 | 期望 |
|---|---|
| 500 封同 (來源→目標) 搬移 | `redundant_selects() == 0`；來源夾 `SELECT` 計數 = 1 |
| 同上 | `command_counts["UID MOVE"]` = ⌈500/MOVE_BATCH_MAX⌉（非 500） |
| `snapshot()` 前後 | 僅目標 uid 變動；他人 `\Deleted` 不被波及 |
| 重連（`arm_expiry` 搬移中途） | 重連後完成、0 重複 / 0 遺漏 |

## 後備搬移冪等（C1，`_move_impl` 後備路徑）

重試前依該 uid 狀態決定（FR-006）：
| 來源狀態 | 動作 |
|---|---|
| uid 已不在來源 | 視為已完成 → 成功返回（no-op） |
| uid 在、已標 `\Deleted` | 跳過 COPY，只補 `UID EXPUNGE`（無 UIDPLUS 才整夾 EXPUNGE） |
| uid 在、未標 `\Deleted` | COPY → 標刪 → `UID EXPUNGE` |

### 引擎斷言
- `arm_expiry` 於「COPY 後、EXPUNGE 前」注入中斷 → 重連重試 → 目標夾複本數**正好 1**、來源正確移除、他人 `\Deleted` 不被波及。
- feature 006 的 `test_fallback_move_idempotency_across_copy_known_limitation`（xfail strict=False）→ 修好後**自動 xpass、移除 marker**。
