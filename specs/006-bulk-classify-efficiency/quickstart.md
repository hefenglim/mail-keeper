# Quickstart — Validation Guide: Bulk Classify Efficiency (Phase 1)

證明本功能端到端可用的可執行驗證情境。全程**離線**：跨 seam 走 IMAP 模擬器引擎（`tests/imap_server.py::ImapServer` + `SimIMAP4_SSL`），分類邏輯走 `FakeBackend`。細節見 [data-model.md](./data-model.md) 與 [contracts/](./contracts/)。

## Prerequisites

```bash
pip install -e ".[test]"
```

## 核心驗證情境（對應 Success Criteria）

### V1 — 存在性檢查不再整夾抓標頭（SC-001 / SC-003）｜對應 US1
- **Given**：`bulk_server(n)`（或自建 n=數千封 INBOX）+ 一份引用部分 UID 的分類工作表；以 `connected_client` 走真 `OutlookIMAPClient` over 引擎。
- **When**：`classifier.build_report(client, rows, cache=...)`。
- **Then**：`server.loop_report()["fetches_per_folder"]` 中該來源夾的「整夾 header FETCH」= 0；`command_counts` 出現 `UID SEARCH`、無整夾 `UID FETCH`；`bytes_*` 較「以 `list_headers` 取 uid」同情景大降（≥90%）。

### V2 — 報告判定等價現況，含已標刪視為存在（SC-002）｜US1、Clarify Q1
- **Given**：母版 `fresh_server()`（含 `INBOX_USER_DELETED_UID` 已標 `\Deleted`）。
- **When**：對引用該 UID 與其他 UID 的工作表跑 `build_report`。
- **Then**：逐列 skip/candidate/infeasible 與原因，與優化前一致；引用已標刪 UID 的列為 **candidate**（視為存在）。

### V3 — 每來源夾現存查詢只一次（FR-002）｜US1
- **Given**：多列、同一來源夾的工作表，共用一個 `ClassifyCache`。
- **When**：`build_report` 後接 `execute`。
- **Then**：`fetches_per_folder` 該夾整夾 header FETCH=0 且 `UID SEARCH` 對該夾僅 1 次；`redundant_full_folder_reads == {}`。

### V4 — 候選分組處理、輸出依 CSV 列序（SC-004 / SC-005 / SC-008）｜US2、Clarify Q3
- **Given**：來源夾交錯（A、B、A、B…）的工作表（`FakeBackend`）。
- **When**：`execute(items, cache=...)` 執行兩次。
- **Then**：處理順序同夾相鄰且兩次一致（決定性）；回傳 `list[MoveResult]` 順序 = 原 CSV 列序；最終搬移結果集合與「未分組」版本一致。

### V5 — 進度不退化（SC-007）｜US1、Clarify Q2
- **Given**：注入一個記錄 `(done,total)` 的 on_progress。
- **When**：存在性查詢執行。
- **Then**：收到 determinate 進度且推進至 `done==total`（total=該夾郵件數）；非「無回饋」。

### V6 — 重連相容（FR-011）｜US1
- **Given**：`server.arm_expiry(before_op="search", nth=1, mode="eof")` + `token_provider`。
- **When**：`list_uids` / `build_report`。
- **Then**：透明重連後回正確 UID 集合、不重複/不遺漏；`loop_report()["authentications"] >= 2`。

### V7 — 內容功能無回歸（SC-006）｜US3
- **When**：`list_headers` / 匯出工作表 / 列出標題。
- **Then**：輸出（含 CJK/emoji/encoded-word 解碼）與優化前逐字一致；破壞性動作仍預設 dry-run、輸出無 secret。

## 執行

```bash
pytest -q                      # 全套離線
mypy src/mailkeeper            # 型別乾淨
pytest --cov=mailkeeper --cov-report=term-missing   # 覆蓋率（imap_client ≥88%）
```

## 交付後（追蹤）
- 回填 `doc/mailkeeper-performance-report-20260627.html` 狀態表：P1/P4 → 已完成 + 版本號；更新卡片與修訂紀錄。
- 同步 `CHANGELOG.md`（升版）與 `memory/roadmap-backlog.md`。
