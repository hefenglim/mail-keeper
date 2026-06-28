# Contract — Backend capability: `list_uids`

新增於 `MailBackend` 協定（後端中立）。`OutlookIMAPClient` 與測試用 `FakeBackend` 皆須實作。

## Signature

```python
def list_uids(
    self, folder: str = "INBOX", *, on_progress: Callable[[int, int], None] | None = None
) -> set[str]: ...
```

## Semantics

- 回傳 `folder` 中**目前現存**郵件的 UID 字串集合。
- 「現存」涵蓋信箱中尚未 expunge 的所有郵件，**包含已標 `\Deleted` 未清除者**（等同 `UID SEARCH ALL`；Clarify Q1）。
- **不得**下載或解析郵件完整標頭/內容——僅取 UID（P1）。
- `on_progress(done, total)`：以該夾郵件數為 `total`、推進至 `done==total` 的 determinate 進度；無人工延遲（D3）。`None` 則不回報。
- 唯讀、可重入：重複呼叫對信箱無副作用；與透明重連相容（中斷後重連重跑、結果不重複/不遺漏；FR-009）。
- 空夾回傳空集合；元素為非空 UID 字串。

## IMAP 實作約束（`imap_client` 內，不外洩）

- `SELECT folder (readonly)` → `UID SEARCH ALL` → 解析空白分隔的 UID。
- 以 `self._with_reconnect(...)` 包裝（有界退避重試）。
- IMAP 細節僅存在於此檔（Principle I）。

## 引擎驗收斷言（測請求端 + log）

以 `OutlookIMAPClient` over `ImapServer`：

| 斷言 | 期望 |
|---|---|
| `command_counts` | 出現 `UID SEARCH`；**無**該夾的整夾完整標頭 `UID FETCH` |
| `loop_report()["fetches_per_folder"]` | 該夾「整夾 header FETCH」= 0（存在性路徑） |
| 回傳集合 | 含母版中已標 `\Deleted` 的 UID（驗 Q1 語意） |
| `bytes_*` | 較 `list_headers` 同夾大幅下降（SC-003 佐證） |
| `arm_expiry(before_op="search"/"fetch")` 後 | 重連後仍回正確集合、不重複/不遺漏（FR-009） |

## 既有方法不受影響

`list_headers` / `list_inbox_headers`（功能1/2 需內容）維持原行為與回傳 `list[MailHeader]`（FR-006、SC-005）。
