# Quickstart / Validation: 輸出體驗優化

驗證三點優化端到端可用。離線測試為主，真實信箱情境為輔。

## 前置

```powershell
$env:PYTHONPATH = 'src'; $env:PYTHONUTF8 = '1'
python -m pytest tests/ -q          # 全離線
python -m mypy src/mailkeeper       # 型別
```

## 情境 1 — Excel 開啟不亂碼（US1 / FR-001,002,003 / SC-001,004）

1. 匯出含多國語文（中／英／日／韓／阿拉伯文＋emoji）的工作表 CSV（離線測試以 `csv_io.write_worksheet` 直接驗證）。
2. **預期**：檔首為 UTF-8 BOM（`EF BB BF`）；用 Excel 開啟所有字元正確、無亂碼；用文字編輯器亦正常。
3. 把該檔（含/不含 BOM 兩版）交給 `csv_io.read_worksheet`。**預期**：兩版皆 0 解析錯誤、標頭與第一欄無殘留字元。

## 情境 2 — 檔名自動補副檔名（US2 / FR-004,005,006 / SC-002）

1. 於功能 1/2 的輸出路徑輸入 `inbox`（無副檔名）。
2. **預期**：實際寫出 `inbox.csv`，確認訊息顯示 `inbox.csv`。
3. 輸入 `inbox.csv` → 不變；輸入 `data.txt` → 不變（不被改成 .csv）；`report.` → `report.csv`。

## 情境 3 — 大資料夾即時進度（US3 / FR-007,008,009,016 / SC-003）

1. 對 > 30 封（如 400+）的資料夾執行 `export-worksheet`（互動 TTY）。
2. **預期**：標頭下載期間進度以 `\r` 就地更新（done/total 與 %），分批前進、不出現 >數秒無回饋空窗；結束補換行收尾。
3. 執行 `classify --in <worksheet>.csv --run`（多列待搬）。**預期**：搬移逐封更新進度。

## 情境 4 — 門檻與非互動降級（FR-010,011,012,015 / SC-005）

1. 對 ≤ 30 項的操作執行匯出。**預期**：不顯示進度（門檻）。
2. 以非互動方式執行（輸出導向檔案 / 管線 / 非 TTY）。**預期**：不輸出進度控制字元、不阻塞、不污染 CSV；操作正常完成。
3. 迴圈中途某封失敗（離線以 FakeBackend 模擬）。**預期**：進度乾淨收尾（無半截）、錯誤循既有錯誤邊界浮現、全程不崩潰。

## 對應測試

| 情境 | 測試檔 |
|------|--------|
| 1 編碼/BOM 往返 | `tests/test_csv_io.py` |
| 2 檔名補完 | `tests/test_csv_io.py`（`ensure_csv_suffix`）、`tests/test_cli_csv_flow.py`（確認訊息） |
| 3/4 進度行為 | `tests/test_progress.py`（門檻/TTY/非TTY/收尾/不崩潰）、`tests/test_classifier.py`（execute on_progress）、`tests/test_backend.py`（`_chunked`、`list_headers` on_progress 相容） |

## Definition of Done（本功能）

spec+plan+tasks committed · `analyze` clean · 全測試綠（離線）· `mypy` clean · **SR = APPROVE** · CHANGELOG 0.5.0（真實日期）+ 版本雙處同步。
