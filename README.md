# MailKeeper

替你看管收件匣 —— 登入 Outlook.com IMAP、讀取郵件標題、依規則自動整理的 Python 工具。自 v0.4.0 起提供互動選單與三個 CSV 功能：匯出分類工作表、匯出資料夾清單、依 CSV 檢查並搬移分類。

> ⚠️ Outlook.com 已停用 Basic Auth（帳密直連）。本工具一律走 **OAuth2 / XOAUTH2**，需先註冊一個 Azure 應用程式。

## 專案結構（src layout）

```
mailkeeper/
├── pyproject.toml          # 套件設定 + console 指令
├── CHANGELOG.md
├── requirements.txt
└── src/mailkeeper/
    ├── __init__.py         # 對外 API 與 __version__
    ├── __main__.py         # 支援 python -m mailkeeper
    ├── config.py           # 程式碼預設（authority / scopes / IMAP host/port/timeout）
    ├── config_store.py     # 讀寫工作目錄的 config.json（client_id / email + 可選覆寫）
    ├── console.py          # 跨平台防崩潰輸出（UTF-8 + 安全寫入器）
    ├── auth.py             # OAuth2 認證（MSAL device code flow + token 快取）
    ├── imap_client.py      # 所有 IMAP 連線與操作封裝在此（核心隔離模組）
    ├── organizer.py        # 整理規則引擎；只依賴抽象介面 MailBackend
    ├── csv_io.py           # 工作表/資料夾清單 CSV 讀寫（固定英文表頭、UTF-8、標準跳脫）
    ├── classifier.py       # 功能3 分類引擎（檢查報告 / 確認後搬移，只依賴 MailBackend）
    ├── menu.py             # 互動選單路由
    └── cli.py              # 進入點，組裝上述模組（選單 + 子指令）
```

> 調整「整理需求」→ 改 `organizer.py` / `cli.build_rules()`
> 換掉底層協定（例如改用 Graph API）→ 只要新做一個符合 `MailBackend` 介面的類別，上層完全不動。

## 安裝

開發模式（可邊改邊跑）：

```bash
pip install -e .
```

最簡單 —— 從 GitHub Release 一鍵安裝（複製即用，需 Python ≥ 3.10）：

```bash
pip install --user https://github.com/hefenglim/mail-keeper/releases/download/v0.4.0/mailkeeper-0.4.0-py3-none-any.whl
```

最新版本見 [Releases 頁面](https://github.com/hefenglim/mail-keeper/releases)。或安裝本機建置好的 wheel：

```bash
pip install dist/mailkeeper-0.4.0-py3-none-any.whl
```

## 一次性設定：註冊 Azure 應用程式

1. 進入 **Microsoft Entra 系統管理中心 → 應用程式註冊 → 新增註冊**
2. 支援帳戶類型選 **「個人 Microsoft 帳戶」**（或含工作/學校帳戶）
3. 不需填 redirect URI（用 device code flow）
4. 複製 **應用程式 (用戶端) 識別碼**（client_id）—— 首次執行 `mailkeeper` 會在工作目錄產生 `config.json`，把它與你的信箱 `email` 填進去
5. **驗證 → 進階設定 → 允許公用用戶端流程 → 是**
6. **API 權限**：授予委派權限 `IMAP.AccessAsUser.All`（範圍 `outlook.office.com`）

## 使用

安裝後可用 console 指令：

```bash
mailkeeper
```

或：

```bash
python -m mailkeeper
```

**首次執行**會在目前工作目錄產生 `config.json` 範本並提示填寫；填入 `client_id` 與 `email` 後再執行一次即可。
（進階：可在 `config.json` 加 `imap_host` / `imap_port` / `timeout` 覆寫預設值。）

接著會印出「請開啟網址並輸入代碼」的訊息，登入後 token 會快取，之後不必再登入。
若實際登入的帳號與 `config.json` 的 `email` 不一致，MailKeeper 會主動詢問你要如何處理（用登入帳號／保留設定／中止）。
互動執行 `mailkeeper` 會進入**選單**（見下節「選單與 CSV 功能」）。其中「依 CSV 搬移分類」為破壞性動作，預設只產生檢查報告（dry-run），加 `--run` 或於互動中確認後才真的搬移。

## 選單與 CSV 功能（v0.4.0）

直接執行 `mailkeeper`（互動）會出現選單；三個功能也可用子指令（適合自動化）：

```bash
# 1) 匯出某資料夾的「分類工作表」CSV
#    欄位：uid,current_folder,target_folder,date,from,to,subject
mailkeeper export-worksheet --folder INBOX --out worksheet.csv

# 2) 匯出所有資料夾清單（供填寫 target_folder 參考）
mailkeeper export-folders --out folders.csv

# 3) 編輯 worksheet.csv 的 target_folder 後，檢查 → 確認 → 搬移
mailkeeper classify --in worksheet.csv          # 預設只出檢查報告（不搬）
mailkeeper classify --in worksheet.csv --run    # 確認無誤後實際搬移
```

工作流：人或 AI 在工具外編輯 `worksheet.csv` 的 `target_folder`（參考 `folders.csv`），功能3 對「有變動」的列產生可行性檢查報告，確認後才依 CSV 搬移分類。搬移為破壞性動作、預設 dry-run。

> **v0.5.0**：CSV 改用 UTF-8 + BOM，Excel 可直接開啟、中文不亂碼；檔名免打副檔名（自動補 `.csv`）；大資料夾（> 30 封）讀取/搬移時顯示即時進度，不再像當機。

## 當套件匯入使用

```python
from mailkeeper import OutlookIMAPClient, MailOrganizer, Rule, subject_contains
```

## 安全提醒

- `token_cache.bin` 含有效憑證，已列入 `.gitignore`，請勿提交。
- `config.json` 屬使用者本機設定（client_id / email），也已列入 `.gitignore`；token 絕不會寫入其中。
- access token 有時效，過期會自動用 refresh token 靜默更新。
