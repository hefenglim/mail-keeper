# IMAP 模擬器重打造設計文件（方案 B：線級伺服器引擎 + 真 imaplib 轉接）

> 狀態：設計定案、實作進行中。分支 `chore/imap-server-sim`。
> 決策：使用者拍板**全壓方案 B（甲）**，P1–P4 完整建引擎 + 轉接 + 門面 + 遷移 + 助手。
> 目標：整個專案所有 imaplib 應用都跑**真的 `imaplib.IMAP4_SSL`**（上層與真實一模一樣），
> 模擬器是一支**有狀態的記憶體 IMAP 伺服器**，最大化記錄一切有價值的驗證/效能/流程數據，
> 供測試結果分析與 loop regression 分析。

## 1. 為什麼是方案 B（從 imaplib 2.60 原始碼研究得證）

imaplib 的命令方法（`list`/`select`/`fetch`/`uid`…）是**薄包裝**；真正的協定解析、狀態機、
literal 處理、錯誤包裝**全在「可覆寫的傳輸六法」的讀取路徑**裡：

- 可覆寫傳輸：`open` / `read(size)` / `readline` / `send` / `shutdown` / `_create_socket`。
- `_command`→`_command_complete`→`_get_response`→`_get_line`：組 `tag NAME args`、送出、讀回應、
  讀 literal（`self.read(size)`）、解析 `[CODE]`、把錯誤包成 `command: NAME => …`。
- `_get_line` 空行→`abort('socket error: EOF')`；無 CRLF→`abort('unterminated line')`。
- 狀態機 LOGOUT→NONAUTH→AUTH→SELECTED；`_connect` 讀 welcome 後**送 CAPABILITY**。
- `select(readonly=True)` 實送 **EXAMINE**；回 `(typ, untagged['EXISTS'])`。
- `uid` 回應 key 在 `FETCH`（除 SEARCH/SORT/THREAD）。
- `authenticate`：`_Authenticator(authobject)` 做 base64 續傳（XOAUTH2）。

**只要把 socket 換成記憶體伺服器，其餘全部由真 imaplib 執行** → 保真度自動且完整、零漂移，
並自動涵蓋現況高階假物繞過的真實行為（EOF/abort 包裝、EXAMINE、CAPABILITY、response code、
AUTHENTICATE 續傳）。`tests/imaplib_probe.py::ScriptedIMAP4` 已是此模式雛形。

## 2. 模組架構

```
tests/
├── imap_server.py     # ★ IMAP 伺服器狀態引擎（核心）
├── imap_transport.py  # ★ 真 imaplib 子類，只覆寫傳輸六法，接到引擎
├── imap_dataset.py    # 母版資料集 → 建構 ImapServer 狀態（沿用既有 uid/flags/folders）
├── imap_sim.py        # 高階測試門面：fresh_sim()/connected_client()/snapshot/log 查詢，轉呼叫 server
└── imaplib_probe.py   # 保留：引擎序列化 vs 真 imaplib 交叉驗證（防引擎偏差）
```

### 2.1 `imap_server.py` — 伺服器狀態引擎
- **狀態**：`mailboxes: dict[str, list[SimMessage]]`、per-mailbox `uidnext` 與 `uidvalidity`、
  `selected`/`readonly`、`capabilities`、`authenticated`、連線是否存活。
- **入口**：`feed(data: bytes) -> bytes`——接收 imaplib `send()` 來的位元組，解析**一條完整命令**
  （含 literal 續傳，如 AUTHENTICATE/APPEND），執行 handler，序列化回應位元組（untagged 行 + literal +
  tagged `OK/NO/BAD`）。連線層的失效注入也在這裡（回空 bytes / 拋 OSError / 回 BYE）。
- **handler 註冊表**：`{command: handler}`，每個 handler 回 `(untagged_lines, tagged_status, response_code)`。
  新命令 = 加一個 handler（易擴充）。
- **序列化**：產出與真 imaplib 預期一致的 wire bytes（`* …`、`{n}` literal、`<tag> OK …`）。
  以 `imaplib_probe` 對拍真 imaplib 驗證每種回應結構逐位元組正確。

### 2.2 `imap_transport.py` — 真 imaplib 轉接
- `class SimIMAP4_SSL(imaplib.IMAP4_SSL)`：覆寫 `open/_create_socket`（不開真 socket）、
  `send`（→ `server.feed()`，把回應入緩衝）、`read/readline`（從緩衝吐 bytes）、`shutdown`（no-op）。
  其餘**全繼承真 imaplib**（命令組裝、literal、狀態機、錯誤包裝、CAPABILITY、AUTHENTICATE）。
- `install_server(monkeypatch, server)`：把 `mailkeeper.imap_client.imaplib.IMAP4_SSL` 換成
  綁定該 `server` 的 `SimIMAP4_SSL`。上層產品程式**零改動**。

### 2.3 `imap_sim.py` — 高階門面（相容既有測試 API）
- `fresh_sim(**opts) -> ImapServer`（從母版深拷貝）、`connected_client(monkeypatch, server, **kw)`
  （install + 真實 `OutlookIMAPClient` + connect）、`client_on`、`snapshot()`、log 查詢助手。
- 盡量沿用現有測試對 `sim.log`/`snapshot()` 的呼叫形狀，降低遷移成本。

## 3. 最大化驗證數據收集（使用者核心要求）

引擎記錄三層 + 分析助手，全部供 **loop regression 分析**：

1. **原始 wire transcript**：每筆 C→S / S→C 位元組（含 tag），可重播、可 diff、可貼 issue。
2. **結構化命令 log**：`ServerOp(seq, tag, command, args, mailbox, affected_uids, result_typ, response_code, t_wall)`
   —— 比現況 `ImapCommand` 多了 tag/影響 UID/結果碼/時間。
3. **狀態快照** `snapshot()`：各夾 `(uid, frozenset(flags))`，前後比對資料變動。
4. **效能 / 邏輯流程分析數據**（瓶頸分析）：
   - `roundtrips()` 往返總數、`command_count(name)` 各命令次數、每命令耗時/累計。
   - **冗餘偵測** `assert_no_redundant_full_folder_read()`（同夾 SELECT+FETCH-all > 1 即紅）。
   - **請求不變量** `assert_all_fetches_request_uid()`（釘死 0.5.x UID 致命回歸類）。
   - `bytes_in/bytes_out`、最大 literal 大小（記憶體壓力指標）。
   - `dump()`：失敗時一次吐 transcript + 命令 log + 快照 diff（除錯極快）。

## 4. 失效注入「做在傳輸層」（比現況更真）

| 注入（server/transport 層） | 真 imaplib 自動產生 | 覆蓋產品 `_is_session_lost` 分支 |
|---|---|---|
| 讀到空 bytes（socket 關閉） | `abort('socket error: EOF')` | abort（EOF 連環，現況主測）|
| `recv` 拋 `socket.timeout`/`OSError` | OSError 上浮 | OSError（**現況未測**）|
| `ssl.SSLError` | SSLError 上浮 | SSLError（**現況未測**）|
| tagged `NO`/`BAD` 帶 `AUTHENTICATIONFAILED` | `error`/`abort` 含標記 | marker（**現況未測**）|
| `* BYE …` | `_check_bye`→abort | BYE |
| 截斷 literal（少給 bytes） | read/parse 異常 | 真實截斷 |

一套傳輸層注入 → 自動驗證產品重連偵測的**全部真實入口**。

## 5. YAGNI 範圍（守住）

**完整實作（產品用到）**：CAPABILITY 交握、AUTHENTICATE XOAUTH2 續傳、NOOP、SELECT/EXAMINE
（含 EXISTS/UIDVALIDITY 回應）、LIST（mUTF-7）、UID SEARCH ALL、UID FETCH `(UID BODY.PEEK[...])`
（literal）、UID MOVE/COPY/STORE/EXPUNGE、CREATE、LOGOUT、response code
`[NONEXISTENT]/[TRYCREATE]/[COPYUID]/[READ-ONLY]`。

**先跳過（留 handler 註冊點，日後一行加上）**：IDLE、APPEND、SORT/THREAD、ACL/QUOTA、
CONDSTORE/QRESYNC、STATUS、NAMESPACE。

**順帶擬真硬化**：encoded-word 只編顯示名（非整串）、非 PEEK 的 `BODY[...]` 設 `\Seen`、
長表頭折行（驅動 `_unfold`）、母版加 >100 封夾以驅動多批 FETCH。

## 6. 遷移計畫（全程不破紅燈）

- **P1**：建 `imap_server.py`（greeting + CAPABILITY + AUTHENTICATE + SELECT/EXAMINE + LIST +
  UID SEARCH + UID FETCH 唯讀路徑）+ `imap_transport.py`；以 `imaplib_probe` 對拍驗證序列化；
  讓真實 `OutlookIMAPClient.list_headers()` 在引擎上跑通。
- **P2**：引擎補破壞性命令（CREATE/UID MOVE/COPY/STORE/EXPUNGE）+ 傳輸層失效注入。
- **P3**：`fresh_sim/connected_client/snapshot/log` 門面轉呼叫引擎；**逐檔**把測試從舊 `FakeIMAPConn`
  切到新引擎，每批切完跑全綠。
- **P4**：新增分析助手（冗餘/UID 不變量/往返計數/瓶頸/dump）；補擬真硬化與多批資料集。

## 7. 舊 `FakeIMAPConn` 去留（屆時詳細研究再決策）

新引擎若**完整覆蓋**舊高階假物的所有驗證點，則 `FakeIMAPConn` 的重複驗證即非必要 → 評估退場。
**做法**：遷移近完成時，逐一比對「舊 FakeIMAPConn 各測試覆蓋的行為」是否已被新引擎覆蓋，
列出覆蓋對照表 + 殘留價值（若有），**提供分析結果讓使用者拍板**（不逕自刪除）。

## 8. 驗收

- 上層產品（`imap_client`/`classifier`/`cli`）**零改動**，全部測試改跑真 imaplib over 引擎仍全綠。
- `imaplib_probe` 對拍：引擎序列化與真 imaplib 解析逐位元組一致（FETCH/SEARCH/LIST/SELECT…）。
- 新分析助手可在 loop regression 產出往返/冗餘/瓶頸數據。
- mypy 乾淨、覆蓋率閘門維持。
