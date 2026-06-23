# Tasks: 輸出體驗優化 —— 檔名自動補副檔名、處理進度條、Excel 相容 CSV 編碼

**Feature**: `004-output-ux-progress` | **Target version**: 0.5.0
**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md) · **Contracts**: [contracts/interfaces.md](./contracts/interfaces.md)

TDD 強制（憲法 Principle V）：每個行為先寫**失敗測試**（Red），再寫最小實作（Green），全程離線（FakeBackend + StringIO 串流）。

---

## Phase 1: Setup

- [x] T001 確認變更前 baseline 綠：`$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'; python -m pytest tests/ -q` 與 `python -m mypy src/mailkeeper` 皆通過（記錄為基準）。無新增執行期相依（`pyproject.toml` deps 不變）。

## Phase 2: Foundational

> 三個使用者故事彼此獨立、無共用阻斷性前置工作；直接進入各故事階段。`ProgressCallback`／`CSV_ENCODING` 等型別/常數隨其所屬故事的首個實作任務一併引入。

---

## Phase 3: User Story 1 — Excel 相容 CSV 編碼 (Priority: P1) 🎯 MVP

**Goal**: 匯出的 CSV 在 Excel 與文字編輯器皆不亂碼；讀回容忍有無 BOM。

**Independent Test**: 匯出含 ≥5 語文+emoji 的 CSV → 檔首有 BOM、各字元正確；含/不含 BOM 兩版皆能被 `read_worksheet` 0 錯誤解析、無殘留字元。

- [x] T002 [P] [US1] 失敗測試：`write_worksheet`/`write_folders` 寫出檔首含 UTF-8 BOM（`\xef\xbb\xbf`）；多國語文（中/英/日/韓/阿拉伯+emoji）round-trip 正確；`read_worksheet` 對「含 BOM」與「不含 BOM」兩版皆 0 錯誤、標頭/第一欄無殘留 — 在 `tests/test_csv_io.py`。
- [x] T003 [US1] 實作：`src/mailkeeper/csv_io.py` 新增常數 `CSV_ENCODING = "utf-8-sig"`，`write_worksheet`／`write_folders`／`read_worksheet` 改用之；欄序/跳脫/覆寫/必要欄邏輯不變。跑 T002 轉綠。

---

## Phase 4: User Story 2 — 檔名未填副檔名時自動補 `.csv` (Priority: P2)

**Goal**: 任何 CSV 檔名輸入若無副檔名自動補 `.csv`，並於確認訊息顯示實際檔名。

**Independent Test**: 輸入 `inbox` → 寫出 `inbox.csv` 且訊息顯示 `inbox.csv`；`inbox.csv`/`data.txt` 不變。

- [x] T004 [P] [US2] 失敗測試：`csv_io.ensure_csv_suffix` 各邊界 —— `inbox`→`inbox.csv`、`inbox.csv` 不變、`data.txt` 不變、`report.`→`report.csv`、`out/inbox`→`out/inbox.csv`、`a.b.c` 不變 — 在 `tests/test_csv_io.py`。
- [x] T005 [US2] 實作純函式 `ensure_csv_suffix(name: str) -> str`（`os.path.splitext`；有非空且非 `.` 副檔名則原樣，否則 `name.rstrip(".")+".csv"`）— 在 `src/mailkeeper/csv_io.py`。跑 T004 轉綠。
- [x] T006 [P] [US2] 失敗測試：`cli.export_worksheet`/`export_folders` 收到無副檔名路徑時寫出 `.csv` 檔、確認訊息顯示補完後檔名；已含副檔名者不變、不重複附加；且 `classify --in inbox`（無副檔名）會讀取 `inbox.csv`（US2 場景3 讀取路徑）— 在 `tests/test_cli_csv_flow.py`。
- [x] T007 [US2] 實作：`src/mailkeeper/cli.py` 於 `export_worksheet`／`export_folders`／`classify` 及互動選單/argparse 路徑取得使用者輸入後先套 `ensure_csv_suffix`，再讀寫並以補完後檔名顯示於確認訊息。跑 T006 轉綠。

---

## Phase 5: User Story 3 — 大量處理即時進度 (Priority: P3)

**Goal**: 標頭讀取（分批 fetch）與搬移在項目數 >30 且互動 TTY 時即時顯示進度；非互動降級不阻塞、不污染資料；錯誤乾淨收尾、不崩潰。

**Independent Test**: TTY+total>30 顯示且分批前進；非TTY/total≤30 不顯示；context 正常/例外皆乾淨收尾。

> 註（C2 已查證）：匯出走 `cli.export_worksheet → backend.list_headers`、分類走 `classifier.build_report/execute → backend.list_headers`，皆為本階段擴充對象；`list_inbox_headers` 僅 legacy `run_listing` 使用（選單/子指令不走），且其本身委派至 `list_headers`。

- [x] T008 [P] [US3] 失敗測試：`progress.reporter` —— (a) TTY 且 total>30 會輸出且最終收尾換行；(b) 非 TTY 零輸出；(c) total≤30 零輸出；(d) 區塊內例外時仍乾淨收尾並向外傳遞；(e) 輸出層丟例外時被吞、不崩潰；(f) 只寫入注入的串流（不碰 stdout）；(g) 標籤含 CJK、且注入串流首次 write 丟 `UnicodeEncodeError` 時被吞、不崩潰（編碼安全，FR-011）；(h) `total=None`（未知總數）時為 no-op、不崩潰 — 在 `tests/test_progress.py`（以 StringIO + `isatty` stub 注入）。
- [x] T009 [US3] 實作 `src/mailkeeper/progress.py`：`ProgressCallback = Callable[[int,int],None]`；`reporter(label, *, stream=None, threshold=30)` context manager → yield `(done,total)` 回呼；門檻/TTY 判定、`\r` 時間節流重繪、離開時收尾、輸出例外吞掉；**預設串流＝console 包裝後的 `sys.stderr`（編碼安全，FR-011）**。跑 T008 轉綠。
- [x] T010 [P] [US3] 失敗測試：`imap_client._chunked(seq, size)` 等分切批（整除、有餘數、空序列、size≥len）— 在 `tests/test_backend.py`。
- [x] T011 [US3] 實作：`src/mailkeeper/imap_client.py` 新增純函式 `_chunked`，`list_headers` 改為分批 `UID FETCH` 並新增 keyword-only `on_progress: ProgressCallback | None = None`，每解析完一封呼叫 `on_progress(done, total)`；同步擴充 `MailBackend.list_headers` 簽章（`src/mailkeeper/organizer.py`）與 `FakeBackend.list_headers` 接受並呼叫 `on_progress`（`tests/conftest.py`）。跑 T010 + 既有 backend 測試轉綠。
- [x] T012 [P] [US3] 失敗測試：`classifier.execute(backend, items, on_progress=spy)` 對每個候選以遞增 `(done,total)` 呼叫 spy（`total=候選數`），dry-run/搬移閘門不受影響 — 在 `tests/test_classifier.py`。
- [x] T013 [US3] 實作：`src/mailkeeper/classifier.py` 的 `execute` 新增 keyword-only `on_progress`，逐封搬移後回報；其餘（自動建夾、stale-UID 失敗隔離）不變。跑 T012 轉綠。
- [x] T014 [P] [US3] 失敗測試：cli 整合 —— 非 TTY 執行 `export-worksheet`/`classify` 時不輸出進度控制字元、不污染 CSV、不阻塞且正常完成；FakeBackend 收到 `on_progress`（被傳入）— 在 `tests/test_cli_csv_flow.py`。
- [x] T015 [US3] 實作：`src/mailkeeper/cli.py` 以 `progress.reporter(label)` 包住標頭讀取（`export_worksheet` 及 `classify` 的報告階段 `list_headers`）與 `classifier.execute`（搬移），把 yield 的回呼以 `on_progress=` 注入。跑 T014 轉綠。

---

## Phase 6: Polish & Cross-Cutting

- [x] T016 [P] 版本雙處同步 0.4.0 → 0.5.0（`pyproject.toml` 與 `src/mailkeeper/__init__.py`）。
- [x] T017 [P] `CHANGELOG.md` 新增 `[0.5.0] - <真實交付日>`：Excel 相容 CSV 編碼（utf-8-sig）、檔名自動補 `.csv`、處理進度條（新 `progress` 模組、`list_headers` 分批 fetch）。
- [x] T018 [P] 文件：`README.md` 補註（CSV 採 utf-8-sig 可直接 Excel 開啟；檔名免打副檔名；大資料夾顯示進度）。
- [x] T019 全套件離線綠 + 型別乾淨：`python -m pytest tests/ -q` 與 `python -m mypy src/mailkeeper`（涵蓋新 `tests/test_progress.py`）。
- [x] T020 [P] 依 `quickstart.md` 走查 4 情境（編碼/BOM 往返、檔名補完、進度顯示與門檻、非 TTY 降級）。

---

## Dependencies & Execution Order

- **Setup（T001）** → 各故事。
- **US1（T002→T003）**、**US2（T004→T005→T006→T007）**、**US3（T008→T009；T010→T011；T012→T013；T014→T015）** 三故事彼此獨立，可依優先序逐一交付；每故事內**測試先於實作**。
- US2 的 T007 與 US3 的 T015 皆改 `cli.py`，須**循序**（同檔）。US3 的 T011 與 T015 牽動 `imap_client`/`cli`，T011 在 T015 之前。
- **Polish（T016–T020）** 於三故事完成後。

## Parallel Opportunities

- 跨故事測試可並行：`T002`(csv_io)、`T004`(csv_io)、`T008`(progress)、`T010`(backend)、`T012`(classifier) 分屬不同檔的 RED 測試（注意 T002/T004/T006 同檔 `test_csv_io.py`，撰寫時循序避免衝突）。
- Polish 的 `T016`/`T017`/`T018` 不同檔可並行。

## Implementation Strategy

- **MVP = User Story 1（P1 編碼修正）**：單獨交付即解決「匯出 CSV 在 Excel 不可讀」的最高影響缺陷。
- 之後增量交付 US2（檔名）、US3（進度）。每個故事完成即為一個獨立可測、可展示的增量。
- 全程守憲法：`imaplib` 僅在 `imap_client`；`on_progress` 為後端中立 `Callable`；`classify` 維持 dry-run 預設；版本/CHANGELOG 誠實。
