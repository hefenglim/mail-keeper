# Quickstart — Validation Guide: Bulk Classify Efficiency (Phase 1: Lean Existence Check)

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
- **Then**：`fetches_per_folder` 該夾整夾 header FETCH=0 且 `UID SEARCH` 對該夾僅 1 次；`redundant_full_folder_reads == {}`；execute 不發出新查詢（重用快取）。

### V4 — 進度不退化（SC-006）｜US1、Clarify Q2
- **Given**：注入一個記錄 `(done,total)` 的 on_progress。
- **When**：存在性查詢執行。
- **Then**：收到 determinate 進度且推進至 `done==total`（total=該夾郵件數）；非「無回饋」。

### V5 — 重連相容（FR-009）｜US1
- **Given**：`server.arm_expiry(before_op="search", nth=1, mode="eof")` + `token_provider`。
- **When**：`list_uids` / `build_report`。
- **Then**：透明重連後回正確 UID 集合、不重複/不遺漏；`loop_report()["authentications"] >= 2`。

### V6 — 搬移路徑與內容功能無回歸（SC-004 / SC-005）｜US2
- **When**：確認後執行搬移；另跑 `list_headers` / 匯出工作表 / 列出標題。
- **Then**：搬移結果集合、逐列成功/失敗與其順序、進度與優化前一致；匯出/列標題輸出（含 CJK/emoji/encoded-word 解碼）逐字一致；破壞性動作仍預設 dry-run、輸出無 secret。

## 執行

```bash
pytest -q                      # 全套離線
mypy src/mailkeeper            # 型別乾淨
pytest --cov=mailkeeper --cov-report=term-missing   # 覆蓋率（imap_client ≥88%）
```

## 交付後（追蹤）
- 回填 `doc/mailkeeper-performance-report-20260627.html` 狀態表：**P1** → 已完成 + 版本號（**P4** 維持未實作，延至 P2/P3）；更新卡片與修訂紀錄。
- 同步 `CHANGELOG.md`（升版）與 `memory/roadmap-backlog.md`。
