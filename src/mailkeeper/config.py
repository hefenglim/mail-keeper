"""程式碼預設值（非機密、不常改）。

使用者專屬設定 (client_id、email，及可選 IMAP host/port/timeout) 改放在
**執行工作目錄下的 config.json**，由 config_store 載入（見 config_store.py 與 README）。
authority / scopes 鎖在這裡、不開放由 config.json 覆寫（改錯會直接破壞 OAuth 認證）。
"""
from __future__ import annotations

# 個人帳號用 consumers；若也要支援公司/學校帳號可改成 common
AUTHORITY = "https://login.microsoftonline.com/consumers"

# IMAP 需要的權限範圍 (offline_access / openid 由 MSAL 自動加上，請勿手動填)
SCOPES = ["https://outlook.office.com/IMAP.AccessAsUser.All"]

# Outlook.com IMAP 伺服器
IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993

# IMAP 連線/讀取逾時 (秒)；避免伺服器無回應時無限卡住。
# 程式碼預設；自 feature 002 起可由 config.json 覆寫。
IMAP_TIMEOUT = 60

# token 快取檔，避免每次都要重新登入
TOKEN_CACHE_PATH = "token_cache.bin"
