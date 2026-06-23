# 發版前真實帳號 Smoke 檢查表（必跑）

> 離線測試（含 `tests/imap_sim.py` 契約測試）能擋住絕大多數錯誤，但**唯一**會碰到真實
> Outlook IMAP 伺服器的，只有這份手動檢查表。**每次發版前必跑一次**，把「伺服器實際行為
> 與我們的假設不符」這一類風險（如 0.5.1 的 UID 全空、`move` 資料遺失）擋在發版之前。
>
> 需要可登入的測試信箱與已設好的 `config.json`。請用**測試帳號或可拋棄的資料夾**，不要拿正式信箱做搬移/刪除測試。

## 前置
- [ ] 安裝待發版本：`pip install --user dist/mailkeeper-<版本>-py3-none-any.whl`（或 `pip install -e .`）。
- [ ] `python -c "import mailkeeper; print(mailkeeper.__version__)"` == 待發版本。
- [ ] `config.json` 指向**測試信箱**。

## 功能1：匯出工作表（UID 不變量 —— 0.5.1 回歸守衛）
- [ ] `mailkeeper export-worksheet --folder Inbox --out smoke.csv`
- [ ] 大資料夾（>30 封）時，過程中**看得到 ASCII 狀態條**即時前進（不像當機）。
- [ ] 開 `smoke.csv`：**每一列 `uid` 欄都有數字、非空**。（這是 0.5.1 致命 bug 的直接驗收點。）
- [ ] 用 Excel 開啟：中文等多國語文**正常顯示、非亂碼**。

## 功能2：匯出資料夾清單
- [ ] `mailkeeper export-folders --out folders.csv` → 內容含預期資料夾、`folder` 表頭。

## 功能3：分類（dry-run → 真搬移，含 move 安全性 —— 0.5.1 資料遺失守衛）
- [ ] 用 `smoke.csv` 改幾列 `target_folder` 指向一個**可拋棄的測試夾**（如 `SmokeTest`）。
- [ ] dry-run：`mailkeeper classify --in smoke.csv`
  - [ ] 初步檢驗（讀來源夾）期間**看得到狀態條**。
  - [ ] 報告列出「將新建的資料夾」（若目標不存在）。
  - [ ] 預設不搬移（顯示預覽訊息）。
- [ ] 真搬移：`mailkeeper classify --in smoke.csv --run`（互動則輸入 y）
  - [ ] 指定的郵件**確實**搬到測試夾。
  - [ ] **信箱內其他郵件未被動到**（特別是先前手動標記刪除的，仍在 —— `move` 的 UID EXPUNGE 守衛）。
- [ ] 重跑同一份 `smoke.csv --run`：已搬走的列標為「不可行 / 已不存在」、**不重複搬移、不崩潰**（冪等）。

## 韌性（可選但建議）
- [ ] 對一個很大的資料夾跑功能1，過程不卡死、可中途 Ctrl-C 乾淨結束。
- [ ] 故意把 `config.json` 的 email 改錯 → 啟動時被攔下並提示（不會默默用錯帳號）。

完成全部勾選才可 `git tag vX.Y.Z`。任何一項異常 → 修正後重跑，不得發版。
