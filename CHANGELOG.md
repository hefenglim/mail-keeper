# Changelog

## [0.4.0] - 2026-06-22
### Added
- 啟動互動選單 + 三個 CSV 子指令：`export-worksheet`（選資料夾 → 匯出分類工作表）、`export-folders`（匯出資料夾清單）、`classify`（依工作表 → 檢查報告 → 確認 → 搬移）。
- `MailBackend` 擴充：`list_folders()`、`list_headers(folder)`；`imap_client` 加資料夾列舉（含 modified-UTF-7 解析）與 `TO` 標頭。
- `MailHeader` 新增 `recipients`（向後相容，附加在最後）。
- 新模組 `csv_io`（固定英文表頭 `uid,current_folder,target_folder,date,from,to,subject`、UTF-8、標準跳脫）、`classifier`（dry-run 檢查報告 + 確認後搬移、來源 UID 失效逐列回報）、`menu`。
### Changed
- `cli` 改為 argparse 子指令 + 無參數時進選單；非互動安全（印用法 + 非零結束、不卡死）。功能3 破壞性動作預設 dry-run，`--run` 或互動確認後才搬移。
### Notes
- 本期為「手動流程」：CSV 由人＋AI 在工具外編輯；LLM 底層自動串接三功能屬未來階段。

## [0.3.0] - 2026-06-21
### Added
- 設定外部化：`config.json`（執行工作目錄，與 `token_cache.bin` 同處）提供 `client_id`、`email`，及可選 `imap_host`/`imap_port`/`timeout` 覆寫；`authority`/`scopes` 仍鎖在程式碼。
- 首次執行自動產生帶 `_README`/`_help_url` 的 `config.json` 範本並指示填寫；未填妥前以非零碼結束、不嘗試登入。
- 未填/佔位/壞 JSON 在登入前清楚攔截（指出欄位與檔案路徑）。
- 帳號不一致驗證：登入帳號與設定 email 不同時互動提問（用登入帳號〔可回寫〕／保留／中止），非互動則安全中止。
### Changed
- `auth.get_access_token(cfg)` 改吃有效設定並回傳 `(token, 已認證 email)`。
- `OutlookIMAPClient` 可接受 `host`/`port`/`timeout`（預設沿用程式碼值）。
- `config.py` 由「使用者編輯點」改為純程式碼預設；使用者改編輯 `config.json`。
### Security
- `config.json` 納入 `.gitignore`（使用者專屬；token 仍只存於 `token_cache.bin`，絕不寫入 config.json）。

## [0.2.0] - 2026-06-21
### Added
- 防崩潰輸出層 `console.py`：啟動將 stdout/stderr 重設為 UTF-8 並以安全寫入器包覆；任何主機/語系或重導向下都不會因編碼崩潰，無法表示的字元以 backslashreplace 佔位。
- IMAP 連線逾時 `IMAP_TIMEOUT`（預設 60 秒），伺服器無回應時快速失敗而非無限卡住。
- device code 登入等待上界為「裝置代碼有效期」（由 Microsoft 端決定、通常約 15 分鐘），目前不可調；silent refresh 路徑不受影響。
- 測試套件（離線）：`_decode()` 語料、輸出安全、CLI 錯誤邊界、IMAP 逾時。
### Changed
- `_decode()` 強化：攤平折疊標題、逐段解碼、宣告字集失敗改用 `charset-normalizer` 偵測回復；永不拋例外、永遠回傳字串。
- `cli.main()` 頂層錯誤邊界：已知失敗（認證/IMAP/網路/逾時）顯示簡潔訊息並以非零碼結束，不噴 traceback、不外洩 token。
### Dependencies
- 新增 `charset-normalizer`（標題字集偵測）。

## [0.1.0] - 2026-06-20
### Added
- 初版打包為正式 Python package（src layout，可 pip 安裝）
- Outlook.com IMAP 透過 OAuth2 / XOAUTH2 登入（auth.py，MSAL device code flow + token 快取）
- IMAP 操作隔離模組 imap_client.py：列出收件匣標題、搬移、標記已讀、加旗標
- 規則式郵件整理引擎 organizer.py，依賴 MailBackend 抽象介面（底層可替換）
- CLI 進入點：`mailkeeper` 指令與 `python -m mailkeeper`
