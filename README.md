# MailKeeper

替你看管收件匣 —— 登入 Outlook.com IMAP、讀取所有郵件標題，並依規則自動整理的 Python 工具。

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
    └── cli.py              # 進入點，組裝上述模組
```

> 調整「整理需求」→ 改 `organizer.py` / `cli.build_rules()`
> 換掉底層協定（例如改用 Graph API）→ 只要新做一個符合 `MailBackend` 介面的類別，上層完全不動。

## 安裝

開發模式（可邊改邊跑）：

```bash
pip install -e .
```

或安裝建置好的 wheel：

```bash
pip install dist/mailkeeper-0.3.0-py3-none-any.whl
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
預設 `dry_run=True` 只顯示會做什麼、不會真的變動信箱；確認規則無誤後，把 `cli.py` 裡的 `organizer.run(dry_run=False)` 打開。

## 當套件匯入使用

```python
from mailkeeper import OutlookIMAPClient, MailOrganizer, Rule, subject_contains
```

## 安全提醒

- `token_cache.bin` 含有效憑證，已列入 `.gitignore`，請勿提交。
- `config.json` 屬使用者本機設定（client_id / email），也已列入 `.gitignore`；token 絕不會寫入其中。
- access token 有時效，過期會自動用 refresh token 靜默更新。
