# Implementation Plan: 輸出體驗優化 —— 檔名自動補副檔名、處理進度條、Excel 相容 CSV 編碼

**Branch**: `004-output-ux-progress` | **Date**: 2026-06-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-output-ux-progress/spec.md`

## Summary

v0.4.0 之後的優化修正版（目標 **v0.5.0**），打磨既有功能 1～3 的輸出體驗，不新增選單功能、不變更 CSV 欄位結構、不動 OAuth：

1. **Excel 相容 CSV 編碼**：`csv_io` 寫入改 `utf-8-sig`（含 BOM），讀取改 `utf-8-sig`（容忍有無 BOM 並剝除）。
2. **檔名自動補副檔名**：新增純函式 `ensure_csv_suffix(name)`，在 `cli` 取得使用者輸入路徑後、呼叫 `csv_io` 前套用，並以補完後的實際檔名顯示於確認訊息。
3. **處理進度條**：新增後端中立模組 `progress.py`；`imap_client.list_headers` 改為**分批 fetch** 並透過後端中立回呼 `on_progress(done, total)` 回報；`classifier.execute` 同樣接受 `on_progress`；`cli` 以 `progress.reporter` 包住、僅在項目數 **> 30** 且互動 TTY 時即時顯示。

## Technical Context

**Language/Version**: Python ≥ 3.10（locked stack）

**Primary Dependencies**: stdlib `csv` / `os.path` / `sys` / `time`；既有 `imaplib`+`email`、MSAL、`charset-normalizer`。**無新增執行期相依**（進度條純 stdlib）。

**Storage**: 本機 CSV 檔（工作目錄）。

**Testing**: `pytest`，全離線：注入 `FakeBackend`（實作 `MailBackend`）＋以 `io.StringIO` 模擬 TTY/非 TTY 串流測進度。

**Target Platform**: Windows / macOS / Linux 終端機（跨平台）。

**Project Type**: 單一 Python CLI（src layout）。

**Performance Goals**: 對 400+ 封郵件的資料夾，下載期間進度即時前進、無 >數秒無回饋空窗（SC-003）。

**Constraints**: 維持後端隔離（Principle I）；進度輸出走 stderr、不污染 CSV 資料輸出；非 TTY 不阻塞、不輸出控制字元；全程不崩潰、不卡死（Principle VI）。

**Scale/Scope**: 5 個既有模組微調 + 1 個新模組；單封信箱數百～數千封郵件量級。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原則 | 評估 | 結論 |
|------|------|------|
| I. Backend Isolation | `imaplib` 仍只在 `imap_client.py`；`list_headers` 僅新增**後端中立**參數 `on_progress: Callable[[int,int],None] \| None`（只 import `typing.Callable`，不 import `progress`/UI）。`progress.py` 不含 imaplib。`MailBackend` 協定以新增可選參數擴充（向後相容）。 | ✅ PASS |
| II. OAuth-Only | 認證流程未變。 | ✅ PASS |
| III. Safe-by-Default | `classify` 仍 dry-run 預設；進度只是回饋、不改變搬移閘門。 | ✅ PASS |
| IV. Secrets Never Leak | 進度只輸出標籤與計數，無 token；CSV 內容未變、無機密。 | ✅ PASS |
| V. Test-First | 三點皆 Red→Green，離線（FakeBackend + StringIO 串流；`ensure_csv_suffix`、`_chunked`、`progress` 皆純函式/可注入）。 | ✅ PASS |
| VI. Crash-Proof & Honest | 進度走既有 console 安全寫入串流（編碼安全）；context manager 確保錯誤時乾淨收尾；非 TTY 不輸出控制字元、不阻塞；版本 0.4.0→**0.5.0** + CHANGELOG 真實日期。 | ✅ PASS |
| Locked Stack | 無新增執行期相依（純 stdlib）→ 不需修憲。 | ✅ PASS |

**結論：無違反，無需 Complexity Tracking。**

## Project Structure

### Documentation (this feature)

```text
specs/004-output-ux-progress/
├── plan.md              # 本檔
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── interfaces.md    # Phase 1（模組介面契約）
└── tasks.md             # /speckit.tasks 產出（非本指令）
```

### Source Code (repository root)

```text
src/mailkeeper/
├── progress.py        # 【新】後端中立進度回報（reporter context manager + (done,total) 回呼；門檻>30、僅 TTY）
├── csv_io.py          # 【改】CSV_ENCODING=utf-8-sig 讀寫；新增 ensure_csv_suffix(name)
├── imap_client.py     # 【改】list_headers 分批 fetch + on_progress 回呼；新增純函式 _chunked
├── organizer.py       # 【改】MailBackend.list_headers 簽章加 on_progress（後端中立）
├── classifier.py      # 【改】execute 加 on_progress（逐封搬移回報）
├── cli.py             # 【改】路徑輸入套 ensure_csv_suffix 並顯示實際檔名；以 progress.reporter 包住 list_headers / execute
└── （其餘不動）

tests/
├── test_progress.py       # 【新】門檻、TTY/非TTY、乾淨收尾、編碼安全、不崩潰
├── test_csv_io.py         # 【改】BOM 寫入/讀取容忍、ensure_csv_suffix 各邊界
├── test_classifier.py     # 【改】execute 呼叫 on_progress(done,total)
├── test_cli_csv_flow.py   # 【改】無副檔名自動補 .csv、確認訊息顯示實際檔名
└── test_backend.py        # 【改】_chunked 純函式；list_headers on_progress 參數相容
```

**Structure Decision**: 沿用既有 src layout 與四層架構（cli → organizer/classifier → MailBackend → imap_client）。進度為**橫切 UI 關注點**，獨立成 `progress.py`，並以「後端中立 (done,total) 回呼」注入各層迴圈；UI 連線只在 `cli` 完成，維持後端與引擎層不認識顯示細節。

## Phase 0 — Research

見 [research.md](./research.md)：四個決策（utf-8-sig 編碼、分批 fetch、進度回呼 vs 直接耦合、`ensure_csv_suffix` 邊界規則）之選擇、理由與替代方案。

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md)：`ProgressCallback` 型別、`progress.reporter`/`Progress` 行為、`ensure_csv_suffix` 規則、`CSV_ENCODING`、`MailBackend.list_headers` 與 `classifier.execute` 的簽章擴充。
- [contracts/interfaces.md](./contracts/interfaces.md)：各模組對外介面契約與向後相容承諾。
- [quickstart.md](./quickstart.md)：端到端驗證情境（Excel 開啟不亂碼、無副檔名補完、大資料夾進度、非 TTY 降級、BOM 往返）。

## Complexity Tracking

> 無 Constitution 違反，無需填寫。
