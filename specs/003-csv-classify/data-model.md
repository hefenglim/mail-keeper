# Data Model — 003 啟動選單與 CSV 郵件匯出／分類

## 領域型別（跨 `MailBackend` 介面）

### MailHeader（擴充）
| 欄位 | 型別 | 說明 |
|------|------|------|
| `uid` | str | 後端不透明識別字（IMAP UID，僅資料夾內唯一） |
| `subject` | str | 已解碼主旨 |
| `sender` | str | From（已解碼） |
| `date` | str | Date 原字串 |
| `recipients` | str = "" | **新增、附加在最後**：To（已解碼）；帶預設值以保留既有 4 位置引數建構。**不可插在 `date` 前**，否則 dataclass 會丟「非預設引數接在預設引數後」並破壞既有呼叫。 |

> 不含 `folder`：匯出時呼叫端已知所讀資料夾，作為 `current_folder`。

### MailBackend（擴充後的協定）
| 方法 | 用途 | 備註 |
|------|------|------|
| `list_folders() -> list[str]` | **新增**：列舉所有資料夾名稱 | 功能2、功能1 選資料夾 |
| `list_headers(folder) -> list[MailHeader]` | **新增**：讀指定資料夾標題 | 功能1；`list_inbox_headers` 轉呼此法 |
| `list_inbox_headers(mailbox="INBOX")` | 既有（保留，轉呼 `list_headers`） | 相容 feature 001/002 |
| `ensure_folder(folder)` | 既有：確保資料夾存在 | 功能3 建立目標 |
| `move(uid, dest_folder, mailbox)` | 既有：跨資料夾搬移（`mailbox` 參數＝來源資料夾；以位置引數傳入） | 功能3 執行 |
| `mark_read`/`flag` | 既有 | 本功能未用 |

## CSV 結構

### 分類工作表（功能1 產出、人/AI 編輯、功能3 消費 —— 同一份檔）
固定欄位順序與表頭：

| # | 欄位 | 由誰填 | 說明 |
|---|------|--------|------|
| 1 | `uid` | 功能1 | 唯一鍵之一（唯讀，勿改） |
| 2 | `current_folder` | 功能1 | 唯一鍵之一、來源資料夾（唯讀，勿改） |
| 3 | `target_folder` | **編輯者** | 目標資料夾；空白或＝`current_folder` 視為無變動 |
| 4 | `date` | 功能1 | 日期（Date） |
| 5 | `from` | 功能1 | 寄件者（From） |
| 6 | `to` | 功能1 | 收件者（To） |
| 7 | `subject` | 功能1 | 主旨（Subject） |

- 編碼 UTF-8、含表頭、標準 CSV 跳脫；唯一鍵 (`current_folder`, `uid`)。

### 資料夾清單（功能2 產出，參考用）
| 欄位 | 說明 |
|------|------|
| `folder` | 資料夾名稱（可作為 `target_folder` 的有效值） |

> 本期僅輸出 `folder` 欄；`count`（郵件數）**不在本期範圍**（避免每資料夾額外計數的成本）。

## 流程實體與狀態

### 分類決策（功能3 每列）
逐列分類為三態：
- **無變動 (skip)**：`target_folder` 空白或等於 `current_folder` → 不動作。
- **可搬移 (move candidate)**：`target_folder` 已填且 ≠ `current_folder`，且通過可行性驗證。
- **不可行 (infeasible)**：缺必要欄、來源郵件不存在、來源資料夾不存在、目標無法建立 → 標示原因、排除。

### 驗證規則
- 必要欄位齊全：`uid`、`current_folder`、`target_folder`。
- 來源資料夾 ∈ `list_folders()`；來源 `uid` ∈ `list_headers(current_folder)`。
- 目標資料夾存在於 `list_folders()` 或可由 `ensure_folder` 建立。

### 狀態轉移
```
讀入 CSV → 逐列分類(skip/candidate/infeasible) → 檢查報告(dry-run，零變更)
        → 使用者確認 → 對每個 candidate：ensure_folder(target)+move → 回報每列成功/失敗
        （或使用者取消 → 零變更結束）
```
