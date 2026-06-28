# Quickstart — Validation Guide: Bulk Move Efficiency & Idempotency (Phase 2)

全程**離線**：跨 seam 走 IMAP 模擬器引擎（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`），分類層走 `FakeBackend`。細節見 [data-model.md](./data-model.md) 與 [contracts/](./contracts/)。

## Prerequisites
```bash
pip install -e ".[test]"
```

## 核心驗證情境（對應 Success Criteria）

### V1 — 免重複 SELECT + 批次 MOVE（SC-001 / SC-002）｜US1
- **Given**：bulk INBOX、500 列「同來源→同目標」候選；真 `OutlookIMAPClient` over 引擎。
- **When**：`classifier.execute`。
- **Then**：`server.redundant_selects() == 0`、來源夾 `SELECT` 計數 = 1；`command_counts["UID MOVE"]` = ⌈500/MOVE_BATCH_MAX⌉（非 500）；500 封全入目標夾。

### V2 — 分組決定性 + CSV 輸出序 + 等價（SC-003）｜US1
- **Given**：來源夾交錯工作表（`FakeBackend`）。
- **When**：`execute` 兩次。
- **Then**：處理順序同夾相鄰且兩次一致；回傳 `MoveResult` 依 CSV 列序；最終搬移結果集合與「未分組逐封」版本一致。

### V3 — 批次部分失敗逐封歸因（SC-004）｜US1
- **Given**：一批中某 uid 來源已不存在。
- **When**：`move_many` 該批。
- **Then**：成功者仍搬、失敗者如實回報；單封失敗不連坐同批其他封；結果與逐封等價。

### V4 — 後備搬移冪等（SC-005 / C1）｜US2
- **Given**：伺服器不支援 `UID MOVE`（走後備）；`arm_expiry` 於「COPY 後、EXPUNGE 前」注入中斷 + `token_provider`。
- **When**：`execute` 該封（透明重連重試）。
- **Then**：`snapshot()` 目標夾該封複本數**正好 1**、來源正確移除；feature 006 的 C1 xfail 測試自動 xpass（移除 marker）。

### V5 — 搬移中途重連續完（SC-006）｜US2
- **Given**：`arm_expiry(before_op="move", nth=k, mode="eof")` + `token_provider`。
- **When**：`execute` 多封。
- **Then**：透明重連後全部完成、0 重複 / 0 遺漏；`loop_report()["authentications"] >= 2`。

### V6 — 不連坐他人 \Deleted（SC-007）｜US3
- **Given**：來源夾另有他人已標 `\Deleted` 的郵件。
- **When**：任何搬移／後備 expunge。
- **Then**：`snapshot()` 該他人郵件全程在、未被連坐清除。

### V7 — 早停語意（SC-010）｜US3
- **Given(a)**：數列來源 uid 皆已不存在（資料失敗）。**Then**：全部如實回報失敗、**其餘候選仍處理**、不早停。
- **Given(b)**：連線中斷且重連用盡。**Then**：`execute` 停止、回報已完成/未完成；不誤判為資料失敗。

### V8 — 進度 / dry-run / secrets（SC-008 / SC-009）｜US3
- 批次搬移進度推進至 `total`；未加執行旗標維持 dry-run（不變更信箱）；輸出無 token。

## 執行
```bash
pytest -q
mypy src/mailkeeper
pytest --cov=mailkeeper --cov-report=term-missing   # imap_client ≥88%
```

## 交付後（追蹤）
- 回填 `doc/mailkeeper-performance-report-20260627.html`：P2/P3/P4 → ✅ v0.6.2、backlog C1/C2 標示已修；更新卡片/最後更新/修訂紀錄。
- 同步 `CHANGELOG.md`（升版 0.6.2）與 `memory/roadmap-backlog.md`、`memory/perf-optimization-report.md`。
