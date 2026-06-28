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

# ── R7 韌性預設（可由 config.json 覆寫；無效則退回這些安全值，見 config_store）──
MAX_CONSECUTIVE_FAILURES = 3   # 連續「真正失敗」達此數 → 停止整體操作
MAX_RECONNECT_ATTEMPTS = 3     # 單一中斷事件最多重連次數
MAX_RETRIES_PER_OP = 2         # 單一操作重連後最多重試次數（保留給細粒度重試）
BACKOFF_BASE_SECONDS = 0.5     # 指數退避起點秒數
BACKOFF_CAP_SECONDS = 8.0      # 指數退避封頂上限秒數（不超過此值）

# ── feature 007：批次搬移上限（程式內固定、不開放設定；可調批量屬延後的 P6）──
MOVE_BATCH_MAX = 200           # 同 (來源→目標) 群一次 UID MOVE 的最大封數，超過則分塊
