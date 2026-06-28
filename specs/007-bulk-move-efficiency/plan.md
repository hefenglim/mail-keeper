# Implementation Plan: 大量分類搬移的效能與冪等（Bulk Move Efficiency & Idempotency, Phase 2）

**Branch**: `007-bulk-move-efficiency` | **Date**: 2026-06-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/007-bulk-move-efficiency/spec.md`

## Summary

把分類「執行搬移」的三層往返浪費一併消除，並修正後備搬移的非冪等：

- **P4 候選分組**：`classifier.execute` 先把候選依 `(current_folder, target_folder)` 穩定分組；逐群處理、輸出 `MoveResult` 還原 CSV 列序。
- **P3 免重複 SELECT（=C2）**：`OutlookIMAPClient` 追蹤目前選取的 `(mailbox, readonly)`，新增 `_ensure_selected()`，僅在未選／夾不同／模式不同時才 SELECT；`connect()`/`_reconnect()` 重置。`_move_impl`/`mark_read`/`flag` 改走它。
- **P2 批次 UID MOVE**：新增後端中立 `MailBackend.move_many(uids, dest, mailbox) -> dict[uid, error|None]`；IMAP 實作以 `UID MOVE <set>` 批次（超過固定上限分塊），批次失敗退回逐封 `move` 以精確歸因；`classifier.execute` 逐群呼叫之。
- **C1 後備冪等**：`_move_impl` 後備路徑（copy→標刪→UID EXPUNGE）改為重試前先查該 UID 狀態——已不在＝前次已完成（成功返回）；仍在且已標 `\Deleted`＝前次已 copy（跳過 COPY、只補刪除+expunge）；否則正常 copy。消除「COPY 後斷線重試 → 重複複本」。
- **早停語意（Clarify）**：改以**連線層級失敗**（重連用盡）為提前停止；單列資料失敗只記為失敗列、不早停、不連坐。移除原「連續資料失敗計數早停」。

技術取向（honor 憲法 Principle I）：SELECT 追蹤、批次 MOVE、後備冪等全是 IMAP 協定細節，**只在 `imap_client.py`**；跨 seam 只傳領域型別（uid 字串、`dict[uid, error]`）。`classifier`/`cli` 僅透過 `MailBackend` 參與、不 import imaplib。批次/重連沿用既有 `_with_reconnect`。**不新增 runtime 相依**。完成後升 **0.6.2**。

## Technical Context

**Language/Version**: Python ≥ 3.10（鎖定）

**Primary Dependencies**: stdlib `imaplib` + `email` · MSAL · `charset-normalizer`。**不新增 runtime 相依**（分組用 stdlib `sorted`/`itertools`）。

**Storage**: 無新增設定（批次大小為程式內固定上限、不開放設定——P6 延後）。

**Testing**: `pytest` 全程離線。跨 seam（`_ensure_selected`/`move_many`/後備冪等）走 **IMAP 模擬器引擎**：以指令日誌 `redundant_selects()`/`command_counts`/`bottleneck()` 驗往返削減、`snapshot()` 前後驗資料正確與「不連坐他人 `\Deleted`」、`arm_expiry(before_op=..., mode=...)` 注入中斷驗重連續完與後備冪等；分類層分組／輸出順序／早停以 `FakeBackend`（含 `move_many` + UID 狀態）驗。

**Target Platform**: Windows / Linux / macOS 主控台 CLI。

**Project Type**: 單一專案 CLI（src layout）。

**Performance Goals**: 500 封同來源夾搬移：來源夾 SELECT 由 N→1；搬移命令往返由 N→⌈N/批⌉。結果集合、逐列輸出（CSV 序）、安全（不連坐 expunge）等價現況。

**Constraints**: 全程離線可測；不新增 runtime 相依；`mypy` 乾淨；secrets 不外洩；Backend Isolation；破壞性動作維持 dry-run 預設與安全鐵則；批次與重連有界。

**Scale/Scope**: 數百～數千封同流程搬移；超大同群分塊。

## Constitution Check

*GATE: 必須於 Phase 0 前通過，Phase 1 設計後再次複查。*

| Principle | 遵循方式 | 結論 |
|---|---|---|
| I. Backend Isolation（NON-NEGOTIABLE）| SELECT 追蹤／批次 MOVE／後備冪等之 IMAP 細節**只在 `imap_client.py`**；新增能力 `move_many` 加在 `MailBackend`、跨 seam 僅傳 uid 字串與 `dict[uid,error]`；`classifier`/`cli` 不特例化後端、不 import imaplib。| ✅ Pass |
| II. OAuth-Only | 不動認證；沿用既有連線/授權與 `_with_reconnect`。| ✅ Pass |
| III. Safe-by-Default | dry-run 預設不變；後備路徑仍「COPY 成功才標刪、UID EXPUNGE 限定該封」，且**更冪等**（重試不重複複本）；批次失敗退逐封不影響安全。| ✅ Pass |
| IV. Secrets Never Leak | 新路徑不記錄/輸出 token；錯誤訊息不含 secret。| ✅ Pass |
| V. Test-First（NON-NEGOTIABLE）| 全部 Red→Green、離線；跨 seam 走引擎（測請求端：UID MOVE 批次、SELECT 次數、後備序列；雙層 log+snapshot；arm_expiry 異常）；分類層走 FakeBackend。| ✅ Pass |
| VI. Crash-Proof & Honest | 重連/批次有界；提前停止改連線層級（更正確）；後備冪等消除重複複本；升版 0.6.2 + 真實日期 CHANGELOG + 回填效能報告。| ✅ Pass |

**無違規** → Complexity Tracking 留空。

## Project Structure

### Documentation (this feature)

```text
specs/007-bulk-move-efficiency/
├── plan.md · research.md · data-model.md · quickstart.md
├── contracts/   (backend-move-many.md, classifier-execute.md)
└── tasks.md     (/speckit.tasks 產出)
```

### Source Code (repository root)

```text
src/mailkeeper/
├── organizer.py     # 改：MailBackend 協定新增 move_many(uids, dest, mailbox) -> dict[str, str | None]（向後相容）
├── imap_client.py   # 改：_selected 狀態 + _ensure_selected()（connect/_reconnect 重置）；_move_impl/mark_read/flag 改走它；
│                    #     新增 move_many + _move_many_impl（UID MOVE <set> 分塊、批次失敗退逐封）；
│                    #     _move_impl 後備路徑改冪等（重試前查 UID 狀態：已不在→成功、已標刪→跳 COPY 只 expunge）
├── classifier.py    # 改：execute 依 (current,target) 分組、逐群 move_many、結果還原 CSV 列序；
│                    #     早停改連線層級（移除連續資料失敗計數）；進度每批 += 該批封數
├── config.py        # 改（可能）：批次上限固定常數（程式內預設、不開放設定）
└── cli.py           # 改（可能）：early-stop/剩餘回報文案配合連線層級語意微調

tests/
├── conftest.py                   # 改：FakeBackend 新增 move_many（含 UID 狀態查詢以驗冪等）
├── test_backend.py               # 增：FakeBackend.move_many 行為（成功/部分失敗 dict）
├── test_imap_loop_regression.py  # 增：500 同夾 → redundant_selects()==0、UID MOVE 批次次數、bottleneck；分組決定性
├── test_imap_server_p2.py        # 增/改：後備冪等（arm_expiry copy 後中斷→重試無重複複本，C1 xfail→xpass 移 marker）；批次部分失敗逐封歸因；不連坐他人 \Deleted
├── test_classifier.py            # 改/增：execute 分組順序 + 結果 CSV 序 + 早停改連線層級（遷移舊「連續資料失敗早停」測試）
└── test_imap_dataset.py          # 改（如需）：效率斷言配合批次/免重選
```

**Structure Decision**: 沿用單一專案 src layout。改動集中於 `imap_client.py`（搬移路徑）與 `classifier.py`（分組/早停），以 `MailBackend.move_many` 後端中立擴充；不新增模組、不新增 runtime 相依。

## Complexity Tracking

> 無憲法違規，無需填寫。
