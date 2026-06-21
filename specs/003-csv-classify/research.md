# Research — 003 啟動選單與 CSV 郵件匯出／分類

Phase 0 的關鍵技術決策（皆已於 spec 釐清或屬合理預設，無未解 NEEDS CLARIFICATION）。

## D1 — `MailBackend` 契約以最小幅度擴充
- **Decision**：新增 `list_folders() -> list[str]` 與 `list_headers(folder) -> list[MailHeader]`；`list_inbox_headers(mailbox)` 改為轉呼 `list_headers`。`MailHeader` 新增 `recipients: str = ""`。`move(uid, dest, source_folder)`、`ensure_folder` 沿用。
- **Rationale**：以「新增方法」擴充契約（憲法 Principle I 允許），不破壞既有簽章；`recipients` 帶預設值，既有 4 位置引數建構與測試不受影響。
- **Alternatives**：把 `folder` 放進 `MailHeader`（否決：匯出時呼叫端已知所讀資料夾）；單一 API 一次回所有資料夾郵件（否決：逐資料夾語意更清楚、記憶體可控）。

## D2 — IMAP 列舉資料夾
- **Decision**：`imap_client` 以 `IMAP4.list()` 取得並解析資料夾名稱（處理引號、階層分隔字元、IMAP modified-UTF-7 名稱），封裝於 `imap_client.py`。
- **Rationale**：列資料夾是標準 IMAP 能力；解析細節屬協定，必須留在隔離層。
- **Alternatives**：硬編資料夾清單（否決：不符「所有資料夾」）。

## D3 — 讀標頭加入收件者
- **Decision**：fetch 由 `HEADER.FIELDS (SUBJECT FROM DATE)` 改為加入 `TO`；沿用 `_decode` 處理編碼。
- **Rationale**：CSV 需要 `收件者(to)`；仍是標頭層級、便宜。
- **Alternatives**：抓內文摘要（否決：使用者決議只出標頭、不讀內文）。

## D4 — CSV I/O 用 stdlib，固定欄位順序
- **Decision**：用 stdlib `csv` 讀寫；固定欄序 `uid, current_folder, target_folder, date, from, to, subject`（全英文表頭）；UTF-8、含表頭。
- **Rationale**：`csv` 正確處理逗號/引號/換行跳脫；零新相依（憲法 Locked Stack）。固定欄序讓功能1 輸出與功能3 讀入一致。
- **Alternatives**：pandas（否決：新相依）；手刻字串拼接（否決：跳脫易錯）。

## D5 — 功能3 預設 dry-run 報告 + 明確確認
- **Decision**：功能3 預設只產生檢查報告（驗證可行性、區分「將搬移／無變動／不可行」），明確確認後才搬移。
- **Rationale**：破壞性動作預設 dry-run（憲法 Principle III）；符合使用者「先驗證後搬」需求。
- **Alternatives**：讀到 CSV 直接搬（否決：違反 dry-run 預設）。

## D6 — 選單 + 子指令，非互動安全
- **Decision**：`cli` 以 `argparse` 提供子指令（`export-worksheet`、`export-folders`、`classify`），無子指令且為 TTY → 進互動選單；非互動（無 TTY）→ 印用法後非零結束。
- **Rationale**：滿足 FR-001（選單）與 FR-002（非互動安全、never-stuck）；同時讓自動化可用子指令。
- **Alternatives**：純互動選單（否決：非互動會卡死或無法使用）。

## D7 — 郵件唯一鍵
- **Decision**：以 (`current_folder`, `uid`) 唯一識別郵件（spec 已鎖定）。
- **Rationale**：IMAP UID 僅資料夾內唯一，跨資料夾需含來源資料夾。
