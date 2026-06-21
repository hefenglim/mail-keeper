# Implementation Plan: 啟動選單與 CSV 郵件匯出／分類

**Branch**: `003-csv-classify` | **Date**: 2026-06-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-csv-classify/spec.md`

## Summary

新增一個啟動互動選單與三個以 CSV 為介面的郵件整理功能：(1) 選一個資料夾並匯出其郵件為「分類工作表」CSV（純標頭欄位）、(2) 匯出所有資料夾清單 CSV、(3) 匯入編輯後的工作表，先產生 dry-run 檢查報告、使用者確認後才依 `target_folder` 跨資料夾搬移。本期為「手動驗證」階段：CSV 由人＋AI 在工具外編輯，MailKeeper 不內建 LLM。技術上以最小幅度擴充 `MailBackend`（新增列舉資料夾、讀取指定資料夾標題），其餘 IMAP 細節仍封裝在 `imap_client.py`；CSV 與分類邏輯為不碰 `imaplib` 的上層新模組。

## Technical Context

**Language/Version**: Python ≥ 3.10
**Primary Dependencies**: 既有（msal、charset-normalizer）＋ stdlib `csv`、`argparse`（無新增第三方相依）
**Storage**: 工作目錄下的 CSV 檔（UTF-8）；沿用 `config.json` / `token_cache.bin`
**Testing**: `pytest`，全離線 —— 擴充 `FakeBackend`（folders + 跨資料夾 headers/move）、`tmp_path` CSV、monkeypatch `input`/`isatty`
**Target Platform**: Windows（含非 UTF-8 主控台）與 POSIX；互動與非互動（管線/CI）皆須安全
**Project Type**: 單一 Python 套件、src layout（CLI 工具）
**Performance Goals**: 標頭層級匯出；大資料夾以既有逐封 fetch（效能優化非本期重點，沿用 R7 方向）
**Constraints**: `mypy`-clean；不改既有破壞性預設（dry-run）；維持 `MailBackend` 隔離；沿用 feature 001 的編碼安全/錯誤邊界與 feature 002 的設定載入

## Constitution Check

*GATE：設計前須通過，設計後再核。*

- **I. Backend Isolation** — ✓ 新 IMAP 操作（`LIST` 列資料夾、`FETCH` 增加 `TO` 標頭、跨資料夾 `UID MOVE`）全部加在 `imap_client.py`；`csv_io`、`classifier`、選單只透過 `MailBackend` 介面與 `MailHeader`／資料夾名稱字串運作。契約以**新增方法**擴充（`list_folders`、`list_headers`），不破壞既有簽章 —— 符合「擴充而非破壞」。
- **II. OAuth-Only** — ✓ 不動認證機制（沿用 feature 002 的 `get_access_token`）。
- **III. Safe-by-Default Destructive** — ✓ 功能3 預設只產出檢查報告（dry-run），明確確認後才搬移；搬移用既有 `move`（UID MOVE，失敗有 copy+delete+expunge 後備）。
- **IV. Secrets Never Leak** — ✓ CSV 僅含標頭類欄位，不含 token；錯誤沿用不洩密邊界。
- **V. Test-First** — ✓ 所有行為（選單路由、CSV 讀寫/跳脫、檢查報告分類、確認後搬移）皆可用 `FakeBackend` + 暫存 CSV 離線測試。
- **VI. Crash-Proof & Honest** — ✓ CSV 壞檔/缺欄/路徑、非互動選單、無效列皆走乾淨處理（沿用 001 邊界）；版本 0.3.0 → 0.4.0、CHANGELOG 真實日期。
- **Locked Stack** — ✓ 僅用 stdlib `csv`/`argparse`，無新相依。
- **MailHeader 擴充** — 新增 `recipients`（收件者）欄位，附預設值，向後相容（既有 4 位置引數建構不受影響）。

**Result**: PASS。無違規；Complexity Tracking 不需要。

## Project Structure

### Documentation (this feature)

```text
specs/003-csv-classify/
├── spec.md
├── plan.md              # 本檔
├── research.md          # Phase 0 決策
├── data-model.md        # Phase 1 實體與 CSV/契約資料結構
├── contracts/
│   └── interfaces.md    # MailBackend 擴充 + CLI/選單 + CSV 格式契約
├── quickstart.md        # 離線驗證與手動端到端流程
└── checklists/requirements.md
```

### Source Code (repository root)

```text
src/mailkeeper/
├── imap_client.py    # EDIT — MailHeader 增 recipients；新增 list_folders()、list_headers(folder)；FETCH 加 TO
├── organizer.py      # EDIT — MailBackend Protocol 增 list_folders()、list_headers()（契約擴充）
├── csv_io.py         # NEW — 工作表/資料夾清單的寫出、工作表的讀入解析（stdlib csv；固定欄位順序）
├── classifier.py     # NEW — 比較 current vs target、產生檢查報告、確認後執行搬移（只依賴 MailBackend）
├── menu.py           # NEW — 互動選單（路由到三功能；非互動安全處理）
├── cli.py            # EDIT — argparse 子指令 + 無參數時進選單；組裝 config/auth/backend
└── __init__.py       # EDIT — 視需要匯出新型別；bump __version__

tests/
├── conftest.py            # EDIT — FakeBackend 增 folders/list_headers/move 記錄；CSV 樣本
├── test_backend.py        # NEW — MailHeader.recipients、_parse_folder_name 解析
├── test_csv_io.py         # NEW — 寫出/讀入、固定欄序、跳脫、壞檔、覆寫
├── test_classifier.py     # NEW — 無變動略過、可行/不可行分類、報告、確認後搬移、部分失敗
├── test_menu.py           # NEW — 選單路由；非互動安全
└── test_cli_csv_flow.py   # NEW — 子指令端到端（注入 FakeBackend）

pyproject.toml / CHANGELOG.md  # EDIT — 版本 0.3.0 → 0.4.0；真實日期條目
```

**Structure Decision**：把三個關注點切成獨立小模組 —— `csv_io`（格式 I/O，純資料）、`classifier`（比較/報告/搬移，只依賴 `MailBackend`）、`menu`（互動路由）。三者皆不認識 `imaplib`，維持隔離；後端能力集中在 `imap_client.py` 並透過 `MailBackend` 暴露。

## Key Technical Decisions（research 摘要，詳見 research.md）

1. **後端契約擴充（最小）**：`MailBackend` 新增 `list_folders() -> list[str]` 與 `list_headers(folder) -> list[MailHeader]`；既有 `list_inbox_headers(mailbox)` 改為轉呼 `list_headers`。`move(uid, dest, source_folder)` 與 `ensure_folder` 已足夠，不需新增。`MailHeader` 新增 `recipients: str = ""`。
2. **IMAP 列資料夾**：`imap_client` 以 `IMAP4.list()` 取得資料夾名稱並解析（處理引號/階層分隔字元/MIME-modified UTF-7 名稱），全部封裝在 `imap_client.py`。
3. **讀標頭加收件者**：既有 fetch 從 `HEADER.FIELDS (SUBJECT FROM DATE)` 改為加入 `TO`；`_decode` 沿用。
4. **CSV I/O**：用 stdlib `csv`（自動處理逗號/引號/換行跳脫），固定欄位順序 `uid, current_folder, target_folder, date, from, to, subject`（全英文表頭）；UTF-8、含表頭。讀入時以表頭對應欄位、容忍多餘欄、缺必要欄則乾淨報錯。
5. **分類引擎（功能3）**：逐列判斷 —— `target` 空白或＝`current` ⇒ 無變動略過；否則為候選。候選驗證：來源資料夾存在、目標資料夾存在或可建立、UID 在來源資料夾存在。產出報告（將搬移／無變動／不可行＋原因）。確認後對每個可行候選 `ensure_folder(target)`＋`move(uid, target, source)`，逐列回報成功/失敗。預設只到報告（dry-run）。
6. **選單與非互動**：`cli` 以 `argparse` 提供子指令（`export-worksheet --folder ... --out ...`、`export-folders --out ...`、`classify --in ...`，`classify` 另需 `--run`/互動確認才實搬）。無子指令時：互動（TTY）→ 進 `menu` 迴圈；非互動 → 印用法並非零結束（never-stuck）。功能3 在互動選單中以「顯示報告→詢問確認」把關；以子指令時預設只報告、需明確旗標才執行。

## Phased Implementation (TDD — Red → Green → Refactor)

- **Phase 1 — 後端能力（基礎）**：`MailHeader.recipients`；`MailBackend` 增 `list_folders`/`list_headers`；`imap_client` 實作（list()、fetch 加 TO）；`FakeBackend` 同步。先寫失敗測試（FakeBackend 行為、_decode 既有不退化）。
- **Phase 2 — CSV I/O（US1/US2 基礎）**：`csv_io` 寫出工作表/資料夾清單、讀入工作表（固定欄序、跳脫、壞檔）。Red→Green。
- **Phase 3 — US1/US2 串接**：選資料夾→匯出工作表；匯出資料夾清單。經 `cli` 子指令/選單。
- **Phase 4 — US3 分類引擎**：`classifier` 報告與搬移（無變動略過、可行/不可行、確認後搬、部分失敗）。Red→Green。
- **Phase 5 — 選單與非互動**：`menu` 路由 + `cli` argparse + 非互動安全；功能3 互動確認串接。
- **Phase 6 — Polish**：版本 0.4.0、CHANGELOG、`mypy`、`__init__` 匯出、docs。

## Testing

全離線。`FakeBackend` 提供可控的 folders、各資料夾 headers、move 記錄；CSV 測試用 `tmp_path`；選單/確認用 monkeypatch `input`/`isatty`。涵蓋：固定欄序與跳脫、壞 CSV、無變動列略過、可行/不可行分類、確認前零變更、確認後正確搬移與部分失敗回報、非互動選單安全結束。

## Risks & Mitigations

- **資料夾名稱解析（IMAP LIST、UTF-7、階層分隔）** → 封裝在 `imap_client`，以實際樣本測試；上層只見資料夾名稱字串。
- **同次 CSV 內順序相依**（某列把郵件搬走後，後續列的來源 UID 失效）→ 報告階段以快照驗證、執行階段逐列回報；文件說明。
- **UID 僅資料夾內唯一** → 以 (`current_folder`, `uid`) 為鍵，已於 spec 鎖定。
- **契約擴充影響既有後端/測試** → `list_inbox_headers` 轉呼 `list_headers("INBOX")` 保持相容；`FakeBackend` 同步更新。

## Notes

- Agent-context `after_plan` hook（`speckit.agent-context.update`）為 optional，略過；改由本指令直接更新 CLAUDE.md 的 SPECKIT 區塊指向本 plan。
- 版本：**0.3.0 → 0.4.0**。
- 本期不含 LLM 自動分類與三功能自動串接（spec 明列為未來階段）。
