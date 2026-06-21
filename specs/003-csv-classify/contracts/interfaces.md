# Contracts — 003 啟動選單與 CSV 郵件匯出／分類

CLI 工具的對外/對內契約：`MailBackend` 介面、CLI 子指令與選單、CSV 檔格式。

## 1. MailBackend（擴充後的穩定界線）

```python
class MailBackend(Protocol):
    def list_folders(self) -> list[str]: ...                                   # 新增
    def list_headers(self, folder: str) -> list[MailHeader]: ...               # 新增
    def list_inbox_headers(self, mailbox: str = "INBOX") -> list[MailHeader]: ...  # 既有 → 轉呼 list_headers
    def ensure_folder(self, folder: str) -> None: ...                          # 既有
    def move(self, uid: str, dest_folder: str, mailbox: str = "INBOX") -> None: ...  # 既有（mailbox = 來源資料夾）
    def mark_read(self, uid: str, mailbox: str = "INBOX") -> None: ...         # 既有
    def flag(self, uid: str, mailbox: str = "INBOX") -> None: ...              # 既有
```

- 契約以新增方法擴充，不破壞既有簽章。`imaplib` 細節僅在 `imap_client.py`。
- `move` 的 `mailbox` 參數即「來源資料夾」；功能3 以 `move(uid, target_folder, source_folder)` 跨資料夾搬移。

## 2. CLI 子指令與互動選單

| 子指令 | 旗標 | 行為 |
|--------|------|------|
| `export-worksheet` | `--folder <name>` `--out <path>` | 匯出指定資料夾的分類工作表 CSV |
| `export-folders` | `--out <path>` | 匯出所有資料夾清單 CSV |
| `classify` | `--in <path>` `[--run]` | 讀工作表 → 檢查報告（預設 dry-run）；`--run` 或互動確認後才搬移 |

啟動行為：
- 有子指令 → 直接執行（適合自動化）。
- 無子指令 + 互動（stdin/stdout 皆 TTY）→ 進**互動選單**：
  1. 匯出資料夾的所有電子郵件列表（功能1）
  2. 匯出資料夾清單（功能2）
  3. 依工作表分類（功能3）
  0. 離開
- 無子指令 + 非互動 → 印用法、以非零碼結束（never-stuck）。

結束碼：成功 0；已知錯誤（設定/認證/IMAP/CSV/路徑）非零並乾淨訊息（沿用 feature 001 邊界）。

功能3 把關：互動選單中先顯示檢查報告再詢問「是否執行（y/N）」；以 `classify` 子指令時預設只報告，需 `--run` 才搬移。

## 3. CSV 格式契約

### 分類工作表（export-worksheet 產出 / classify 讀入）
- 表頭固定順序：`uid,current_folder,target_folder,date,from,to,subject`（全英文，利於 AI／試算表穩健解析）
- UTF-8、含表頭、RFC4180 風格跳脫（含逗號/引號/換行的欄位加引號）。
- `uid`、`current_folder` 唯讀（識別鍵）；`target_folder` 由人/AI 編輯；其餘為參考資訊。
- classify 讀入時：以表頭定位欄位、容忍額外欄；缺 `uid`/`current_folder`/`target_folder` 任一 → 乾淨報錯。

### 資料夾清單（export-folders 產出）
- 表頭：`folder`（本期僅此欄；`count` 郵件數列為未來），UTF-8、含表頭。

## 4. 測試契約（離線）

- `FakeBackend` 需實作 `list_folders`、`list_headers(folder)`、`ensure_folder`、`move`（記錄呼叫），並可注入多資料夾與各資料夾 headers。
- CSV 契約以 `tmp_path` 寫/讀驗證固定欄序與跳脫。
- 功能3 契約：確認前零 `move` 呼叫；確認後僅可行候選有 `move`；不可行/無變動列無 `move`。
