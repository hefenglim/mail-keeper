# Quickstart / 驗證指南 — 003 啟動選單與 CSV 郵件匯出／分類

## 前置
- 已完成 feature 002 設定（工作目錄有有效 `config.json` 與 `token_cache.bin`）。
- 開發安裝：`pip install -e .`，或測試時 `PYTHONPATH=src`。

## 離線測試（不需網路；CI 友善）
```powershell
$env:PYTHONPATH='src'; $env:PYTHONUTF8='1'
python -m pytest tests/ -q          # 全綠，含 003 的 csv_io / classifier / menu / cli flow
python -m mypy src/mailkeeper       # 應為 clean
```
驗證重點（皆以 FakeBackend + 暫存 CSV，無網路）：
- 工作表 CSV 依固定欄序輸出、特殊字元正確跳脫。
- 功能3：`target` 空白/同 `current` 的列零搬移；確認前零 `move`；確認後僅可行候選被搬；不可行列被標示。
- 非互動啟動（無 TTY）印用法後非零結束、不卡死。

## 手動端到端流程（連線、真實信箱）
本期為「人＋AI 編輯 CSV」的手動流程：

1. **匯出資料夾清單**（知道有哪些有效 `target_folder`）
   ```powershell
   mailkeeper export-folders --out folders.csv
   ```
2. **匯出某資料夾的分類工作表**
   ```powershell
   mailkeeper export-worksheet --folder INBOX --out worksheet.csv
   ```
3. **編輯 `worksheet.csv`**：人或 AI Agent 參考 `folders.csv`，為要分類的列填 `target_folder`（其餘留空＝不動）。
4. **檢查報告（dry-run，不會搬）**
   ```powershell
   mailkeeper classify --in worksheet.csv
   ```
   檢視報告：將搬移 / 無變動 / 不可行（含原因）。
5. **確認後執行搬移**
   ```powershell
   mailkeeper classify --in worksheet.csv --run
   ```
   或在互動選單選功能3，看報告後回答確認。

或直接 `mailkeeper`（無參數、互動）→ 選單操作功能 1/2/3。

## 預期結果
- 步驟 1/2 產生 UTF-8 CSV，能用試算表開啟、欄位不錯位。
- 步驟 4 完全不變更信箱。
- 步驟 5 僅把「有變動且可行」的郵件搬到對應資料夾，並回報每列成功/失敗；任何 CSV/路徑問題以乾淨訊息結束、零崩潰。

> 契約與資料結構細節見 `contracts/interfaces.md` 與 `data-model.md`；不在此重複實作碼。
