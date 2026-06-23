# Phase 1 Contracts: 模組介面

CLI 工具的對外/層間介面契約。標註向後相容承諾。

## progress.py（新）

```text
ProgressCallback = Callable[[int, int], None]   # (done, total)

reporter(label: str, *, stream=None, threshold: int = 30) -> ContextManager[ProgressCallback]
    進入：回傳一個 (done,total) 回呼。
    顯示條件：stream.isatty() 且 total > threshold（首次更新時判定並快取）。
    未啟用：回呼為 no-op、零輸出（非 TTY / total ≤ 30 / total 未知未達門檻）。
    離開：曾輸出則補換行收尾；例外照常傳遞；輸出層例外被吞（永不崩潰）。
    通道：僅寫 stream（預設 stderr），不污染 stdout/CSV。
```

## csv_io.py（調整）

```text
CSV_ENCODING = "utf-8-sig"                      # 新常數，讀寫共用

ensure_csv_suffix(name: str) -> str             # 新：無副檔名才補 .csv（見 data-model D4 規則）

write_worksheet(headers, folder, path) -> None  # 編碼改 utf-8-sig；欄序/跳脫/覆寫不變
write_folders(folders, path) -> None            # 編碼改 utf-8-sig
read_worksheet(path) -> list[ClassificationRow] # 編碼改 utf-8-sig（剝 BOM、容忍無 BOM）；其餘不變
```

**相容承諾**：CSV 欄位結構、必要欄、`ClassificationRow` 不變；既有以 utf-8 寫出的舊檔仍可被 `utf-8-sig` 讀回（無 BOM 視為 utf-8）。

## MailBackend（organizer.py）/ imap_client.py（調整）

```text
class MailBackend(Protocol):
    def list_headers(self, folder: str = "INBOX", *,
                     on_progress: ProgressCallback | None = None) -> list[MailHeader]: ...
    # 其餘方法（list_folders / list_inbox_headers / ensure_folder / move / mark_read / flag）不變

# imap_client 內部
_chunked(seq, size) -> Iterator[list]           # 新，純函式（離線可測）
```

**相容承諾**：`on_progress` 為 keyword-only 且預設 `None`；既有不傳此參數的呼叫端行為與 v0.4.0 完全一致。`imaplib` 仍只在 `imap_client`；`on_progress` 為 `typing.Callable`，不 import UI。

## classifier.py（調整）

```text
execute(backend, items, *, on_progress: ProgressCallback | None = None) -> list[MoveResult]
build_report(backend, rows) -> list[ReportItem]   # 不變
```

**相容承諾**：`on_progress` 可選、預設 `None`；dry-run/確認閘門不受影響（Principle III）。

## cli.py（調整，使用者可見行為）

```text
- 所有 CSV 路徑輸入（互動提示 + 子指令參數）→ 先 ensure_csv_suffix → 再讀寫 → 確認訊息顯示補完後檔名。
- 標頭讀取與搬移外層以 progress.reporter 包住並注入 on_progress；僅 >30 且互動 TTY 時顯示。
```
