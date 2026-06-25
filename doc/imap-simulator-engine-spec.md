# IMAP 模擬器引擎 — 規格書（Specification）

> **文件性質**：本檔是 **IMAP 模擬器引擎（IMAP Simulator Engine）的規範性規格書**——定義它的目標、
> 必備能力、一致性鐵則，以及「目前符合度」與「規劃中能力」。
> **狀態**：v2 · 建立 2026-06-25 · 分支 `chore/imap-server-sim`。**§1–§4 全部需求已實現（符合度矩陣全綠，見 §6/§7）。**
> **與其他文件的關係**：
> - `doc/imap-simulator-plan.md` —— 重打造的**過程計畫**（方案 B、P1–P4 的決策與遷移），偏歷史/決策紀錄。
> - 本檔 —— **規範性需求（normative spec）**，是「引擎必須是什麼」的單一真實來源。
> - `CLAUDE.md §7` —— 把本規格濃縮為**開發鐵則**（對接 imaplib 一律走本引擎）。三者一致時以本檔為準。
>
> **誠實聲明（憲法 §2）**：下方第 1–5 節是規格本體。第 6 節 **符合度矩陣** 如實對照「現行程式碼實作到哪」；
> 第 7 節為**能力交付紀錄**。2026-06-25 起矩陣**全綠**——所有需求皆有實作 + 測試。日後新增需求時，凡尚未
> 實作者一律標 `規劃中` 並**不得在測試中假裝其存在**（保持本表為單一真實來源）。

---

## 0. 名詞與座標

| 元件 | 檔案 / 符號 | 角色 |
|---|---|---|
| 伺服器引擎 | `tests/imap_server.py::ImapServer` | 有狀態的記憶體 IMAP 伺服器（位元組進、位元組出） |
| 真 imaplib 轉接 | `tests/imap_transport.py::SimIMAP4_SSL` | 真 `imaplib.IMAP4_SSL` 子類，只覆寫傳輸六法接到引擎 |
| 透明替換接點 | `imap_transport.install_server` / `connected_client` | 把產品建構的 `imaplib.IMAP4_SSL` 換成綁定引擎的子類 |
| 共用資料模型 | `tests/imap_sim.py`（`SimMessage` / 編碼助手） | 郵件模型 + wire 編碼（mUTF-7 / encoded-word / 折行） |
| 母版資料集 | `tests/imap_dataset.py`（`fresh_server` / `bulk_server`） | 高涵蓋率情境語料庫，copy-per-test |
| 對拍真解析器 | `tests/imaplib_probe.py::ScriptedIMAP4` | 以**真 imaplib 解析器**交叉驗證引擎 wire（防偏差） |

**核心不變式**：產品（`imap_client` / `classifier` / `cli`）跑的永遠是**真正的 `imaplib`**；本引擎只在 socket
位置供給位元組。命令組裝、literal 讀取、狀態機、錯誤包裝、CAPABILITY 交握、AUTHENTICATE 續傳**全由真
imaplib 執行**——保真度自動且完整，**沒有臆造回應結構的空間**。

---

## 1. 核心定位與架構目標（REQ-CORE）

| ID | 規範性需求 |
|---|---|
| **REQ-CORE-1（雙重模式 / 無縫切換）** | 引擎與真實 imaplib 對上層提供**同一介面**。測試/驗證模式由引擎完全掌控行為；生產環境走真實 socket。切換僅靠**單一接點**（建構 `imaplib.IMAP4_SSL` 的依賴注入處），**產品程式碼零改動**、不得為測試而分支。 |
| **REQ-CORE-2（產品級狀態機，非單純 Mock）** | 引擎是具**狀態機**的伺服器，而非逐呼叫回放固定值的 Mock。狀態流轉 `NONAUTH → AUTH → SELECTED → LOGOUT`，並持有信箱/UID/旗標/選取夾等真實伺服器狀態。 |
| **REQ-CORE-3（可預期的例外注入）** | 除 Happy Path 外，引擎的核心任務是提供 **deterministic fault injection**：可指定「第 N 次某操作」精確失效，用以嚴格檢驗產品面對網路/伺服器異常時的強健性與例外處理邏輯。 |

> **本專案的「單一開關」如何實現（且優於 spec 字面）**：切換點是測試端的工廠注入
> `install_server(monkeypatch, server)`，把 `mailkeeper.imap_client.imaplib.IMAP4_SSL` 換成綁定引擎的
> `SimIMAP4_SSL`。生產環境**不需任何 env-var 或產品內開關**——產品永遠只建構 `imaplib.IMAP4_SSL`，
> 由測試框架在外層替換 socket。這比「在產品碼放一個 if test/prod 開關」更乾淨，且嚴守
> 憲法 §2「Backend isolation／產品零改動」。

---

## 2. 異常注入矩陣（REQ-FAULT）

引擎必須能**故意製造**三大維度的真實異常。注入做在**傳輸層**：一套注入即由真 imaplib 自動產生對應的
abort/error 包裝，覆蓋產品 `_is_session_lost` 的全部真實入口。

### 2A 網路層與連線異常（Network & Connection Layer）

| ID | 需求 | 實現模式 | 狀態 |
|---|---|---|---|
| **REQ-FAULT-A1（突然斷線 / EOF / RST）** | 在命令、認證、或資料傳輸中途無預警關閉 socket。 | `arm_expiry(mode="eof")`（→ `abort('socket error: EOF')`）、`mode="oserror"`（read 拋 `OSError`）、`arm_truncate`（資料傳輸中途截斷） | ✅ |
| **REQ-FAULT-A2（連線逾時）** | TCP handshake 逾時與指令回應逾時（read/write timeout）。 | `arm_connect_failure(mode="timeout")`（handshake 逾時）+ `arm_expiry(mode="timeout")`（read 逾時，`socket.timeout`） | ✅ |
| **REQ-FAULT-A3（TLS/SSL 失敗）** | 憑證過期、cipher 不匹配等安全層錯誤。 | `arm_connect_failure(mode="tls")`（握手期 `ssl.SSLError`）+ `arm_expiry(mode="sslerror")`（session 期間） | ✅ |
| **REQ-FAULT-A4（網路抖動 / 延遲）** | 可動態設定特定指令延遲時間（模擬慢速網路 / 大附件）。 | `arm_latency(op, seconds)`（虛擬時鐘，不真睡；計入 `timing_report`/`loop_report`） | ✅ |

### 2B IMAP 協定與伺服器回應異常（Protocol & Server Responses）

| ID | 需求 | 狀態 |
|---|---|---|
| **REQ-FAULT-B1（標準狀態碼）** | 精準模擬 `OK` / `NO` / `BAD`。 | ✅ |
| **REQ-FAULT-B2（特定 response code）** | `[AUTHENTICATIONFAILED]`、`[UNAVAILABLE]`、`[OVERQUOTA]` 等。 | ✅ `arm_response(code="UNAVAILABLE"/"OVERQUOTA"/...)` 任意 `NO`·`BAD`+code；`authfail` 產 `[AUTHENTICATIONFAILED]`；另內建 `[NONEXISTENT]/[TRYCREATE]/[COPYUID]/[READ-ONLY]/[ALREADYEXISTS]` |
| **REQ-FAULT-B3（非預期/損毀資料流）** | 回不合 RFC 的 tag 或損毀 MIME，測產品 parser 是否崩潰。 | ✅ `arm_unsolicited`（夾帶非預期/畸形 untagged 行）、損毀 encoded-word 主旨（產品 `_decode` 容錯）、`drop_uid`/`fail_fetch`（不守規矩伺服器） |
| **REQ-FAULT-B4（大併發 / 限流）** | 達連線上限後回拒絕連線訊息。 | ✅ `ImapServer(max_connections=N)`：超限 `on_connect` 回 `* BYE [UNAVAILABLE]` |

### 2C IMAP 狀態機異常（State Machine Violations）

| ID | 需求 | 狀態 |
|---|---|---|
| **REQ-FAULT-C1（非同步狀態變更）** | FETCH 進行中伺服器主動推 `EXPUNGE`（他處已刪）等 untagged 通知。 | ✅ `arm_async_expunge(uid, before_op=...)`：in-flight 命令回應前夾帶 `* <seq> EXPUNGE` 並真實移除 |
| **REQ-FAULT-C2（非法指令順序）** | 未經 AUTHENTICATE 即下需認證指令時的防錯回應。 | ✅ `enforce_state=True`（預設）：AUTH 前 SELECT、未 SELECT 的 UID*/EXPUNGE → `BAD [CLIENTBUG]` |

---

## 3. 可觀測性與自動化驗證機制（REQ-OBS）

為支援高強度 loop regression，引擎內建高精度遙測；測試後由分析模組做「預期行為比對」與「效能瓶頸分析」。

### 3A 多維度價值日誌（Telemetry Matrix）

| ID | 需求 | 實現符號 / 狀態 |
|---|---|---|
| **REQ-OBS-A1（原始協定全紀錄）** | 記錄產品端原始指令與引擎原始回應流（含 tag 匹配）。 | ✅ `wire_in` / `wire_out`（可重播、可 diff） |
| **REQ-OBS-A2（結構化命令 log）** | 每命令的 tag/命令/參數/信箱/影響 UID/結果碼/時間/狀態/延遲。 | ✅ `log: list[ServerOp]`（含 `t_wall,t_mono,injected_latency_s,state_before,state_after`） |
| **REQ-OBS-A3（狀態轉移軌跡）** | 記錄每次連線的狀態變化以驗證合法狀態機操作。 | ✅ `transitions: list[(from,to,cause)]` + `assert_state_machine_legal()` |
| **REQ-OBS-A4（異常注入事件）** | 「故意」延遲/斷線/回 BAD/NO 時，留明確標記（注入類型/觸發時機/受影響命令）。 | ✅ `fault_events: list[dict]`（`kind/op/detail/t_mono`），併入 `loop_report()` |
| **REQ-OBS-A5（時間與效能度量）** | 可注入並度量延遲以驗證 timeout/retry；牆鐘時間戳。 | ✅ `t_wall`（真實牆鐘）+ `t_mono`（**虛擬**單調時鐘，**僅被 `arm_latency` 注入延遲推進**）+ `timing_report()`。**誠實界定**：記憶體引擎無真實網路往返，故提供的是**確定性注入延遲**（驗證 timeout/retry 行為），**非實測 RTT/throughput** |

### 3B 後置自動化分析（Post-Test Analysis）

| ID | 需求 | 實現符號 / 狀態 |
|---|---|---|
| **REQ-OBS-B1（序列與行為驗證）** | 比對產品行為軌跡是否符合預期（如注入認證失敗後「重試且 N 次後放棄並記錄」，而非無窮迴圈）。 | ✅ `assert_sequence(expected, subsequence=True)`（命令/結果碼/response code 序列對齊）+ `loop_report`/`roundtrips` 素材 |
| **REQ-OBS-B2（效能瓶頸審查）** | 檢測重複連線（connection churning）、頻發小指令未批次、延遲升高時 timeout/retry 是否得當。 | ✅ `bottleneck()` / `redundant_selects()` / `redundant_full_folder_reads` / `loop_report()`（往返、各命令次數、bytes、每夾 FETCH） |
| **REQ-OBS-B3（請求不變量）** | 釘死高風險回歸類。 | ✅ `assert_all_fetches_request_uid()`（0.5.x「FETCH 未索取 UID → uid 全空」致命回歸）、`fetch_count()`（同夾 >1 = 冗餘重抓） |

> **鐵則（CLAUDE.md §7）**：任何 bulk-mail / loop 行為（大量 classify/export、重連中迴圈）都**必須**跑在引擎上，
> 並以其 log 分析驗證：`loop_report()` 的 `redundant_full_folder_reads` 須為空、
> `assert_all_fetches_request_uid()` 通過、前後 `snapshot()` 比對。

---

## 4. 具狀態資料集與透明替換（REQ-DATA / REQ-API）

### 4A 情境驅動資料集（Scenario-Driven Fixtures）

| ID | 需求 | 狀態 |
|---|---|---|
| **REQ-DATA-1（預設測試語料庫）** | 多層級信箱、合規與畸形表頭、多種旗標（`\Seen`/`\Deleted`…）。 | ✅ `imap_dataset.master_mailboxes()`：ASCII/CJK/emoji/encoded-word/已讀/已刪/空/超長主旨、巢狀 + CJK 夾名；`bulk_server(n)` 供 >100 封多批 FETCH |
| **REQ-DATA-2（具狀態存取/變更）** | 非唯讀；`COPY/MOVE/STORE/EXPUNGE` 即時更新記憶體狀態，後續 `SEARCH/FETCH` 反映結果。 | ✅ 已實現（`_uid_move/_uid_copy/_uid_store/_uid_expunge` 直接變動 `mailboxes`） |
| **REQ-DATA-3（copy-per-test 隔離）** | 每測自獨立深拷貝母版出發，互不污染。 | ✅ `fresh_server()` / `bulk_server()` |
| **REQ-DATA-4（MIME 內文/附件）** | 純文字/HTML/大型附件等內文型態。 | ✅ `mime_message(text=,html=,attachments=)` / `mime_server()`；引擎服務 `BODY[]`/`BODY[TEXT]`/`RFC822`/`RFC822.SIZE`/`BODYSTRUCTURE`（皆對拍真 imaplib）。產品現只抓 `HEADER.FIELDS`，此地基供未來抓內文/附件即用 |

### 4B 介面等效與無差別替換（API Parity & Drop-in）

| ID | 需求 | 狀態 |
|---|---|---|
| **REQ-API-1（100% 簽名相容）** | 對上層提供與真實 imaplib 完全相同的介面、方法簽名、回傳格式、例外類別。 | ✅ **強於 spec**：上層跑的就是**真 imaplib 本體**，只換 socket → 簽名/回傳/例外皆為 imaplib 原生，零模仿落差 |
| **REQ-API-2（透明隔離）** | 產品開發者撰寫商業邏輯時不需知道底層是模擬器或真伺服器；僅在初始化（DI/Factory）抽換實體。 | ✅ `install_server` / `connected_client` 在測試外層注入，產品零感知 |

---

## 5. 一致性鐵則（Conformance Invariants — 非協商）

開發/擴充引擎時，下列鐵則任一被違反即視為回歸（對應 `CLAUDE.md §7` 與 `doc/lessons-learned.md`）：

1. **測請求，不只測回應。** 斷言「我們送出的 IMAP 命令/參數」（如 FETCH 必含 `UID`），而非只驗解析。
   餵造假回應的 parser 測試對契約零證明力——0.5.1 UID 致命 bug 正出於此缺口。
2. **用線級引擎，不用手刻回應。** 以真 `imaplib.IMAP4_SSL` over `ImapServer` 驅動產品；新增 IMAP 方法時，
   **先加對拍真 imaplib 的保真案例**（`imaplib_probe`），再擴充引擎的真實伺服器行為。
3. **位元組保真，對拍真 imaplib。** 引擎 wire 必須能被真 imaplib 解析器正確解析；回應格式有疑慮時
   **以真 imaplib 原始碼或實跑確認，絕不臆測**（參考源見 §9）。
4. **斷言輸出不變量。** 如每筆 `MailHeader.uid` 非空；破壞性操作未經驗證複本不刪除——寧可大聲失敗，
   不要靜默損毀。
5. **雙層驗證。** 操作後同時斷言：(1) 命令 log（`commands()`/`log`）——派送的命令/參數/順序正確且安全；
   (2) `snapshot()` 前後——資料變動正確且**僅此**（如他人的 `\Deleted` 郵件絕不被波及 expunge）。
6. **母版單一可信來源、copy-per-test。** 新情境擴充母版；非 ASCII 表頭連續段須編成**單一** encoded-word
   （相鄰 encoded-word 會被 `decode_header` 吃掉其間空白）。
7. **loop regression 必走引擎並分析其 log。** 見 §3B 鐵則。
8. **祕密警示（憲法 §4）。** `wire_in`/`wire_out` 與 `dump()` 會記錄 AUTHENTICATE 的 base64 SASL 行，其中含
   `auth=Bearer <token>`；`server.auth_string` 亦持有解碼後的 token。引擎是**純測試**元件、只有**假 token**
   流經，且 `dump()` 僅於測試失敗時觸發——非實際洩漏面。但**切勿**讓引擎對接真實帳號、亦**勿**貼出含真實
   token 的 transcript/`dump()` 輸出。需要時可遮罩 SASL 行。

---

## 6. 符合度矩陣（Conformance — 現況快照 2026-06-25：全綠）

| 維度 | 已實現 ✅ | 部分 ◑ | 規劃中 ○ |
|---|---|---|---|
| 核心定位（§1） | CORE-1, CORE-2, CORE-3 | — | — |
| 連線層異常（§2A） | A1, A2, A3, A4 | — | — |
| 協定回應異常（§2B） | B1, B2, B3, B4 | — | — |
| 狀態機異常（§2C） | C1, C2 | — | — |
| 遙測（§3A） | A1, A2, A3, A4, A5 | — | — |
| 後置分析（§3B） | B1, B2, B3 | — | — |
| 資料集（§4A） | DATA-1, 2, 3, 4 | — | — |
| 透明替換（§4B） | API-1, API-2 | — | — |

**判讀**：**§1–§4 全部需求皆 ✅ 已實現且有測試**（引擎自測 + 對拍真 imaplib + 產品端行為）。2026-06-25
完成 E1–E11 一次性強化（見 §7），矩陣由「核心綠、邊角 ◑/○」推進到**全綠**——任何對接 imaplib 的開發
（正常與非正常情境）皆有萬全底層模擬器可實測。測試檔：`test_imap_server_faults.py`（E1–E10）、
`test_imap_server_mime.py`（E11）、既有 `test_imap_server*.py` / `test_imap_loop_regression.py`。

---

## 7. 能力交付紀錄（Delivered — 引擎強化）

下列 E1–E11 為把引擎推向 §1–§4 完整目標的強化，**2026-06-25 一次性交付完成**（分支
`chore/imap-server-sim`，純測試基建、產品碼零行為變更）。與產品韌性修正（見 `roadmap-backlog` 的
R7-followup C1/C2）分流。每項皆遵守 §5 鐵則 + §9 對拍。

- ✅ **E1 延遲/抖動注入**（A4）：`arm_latency(op, seconds)` 虛擬時鐘（不真睡）+ `timing_report()`；
  讀逾時 `arm_expiry(mode="timeout")`（`socket.timeout`）。
- ✅ **E2 連線期專屬失敗模式**（A2/A3）：`arm_connect_failure(mode="timeout"/"tls"/"refused"/"bye")`。
- ✅ **E3 截斷 literal 中途斷**：`arm_truncate(op, drop=)` → 真 imaplib 受控 abort；產品透明重連復原。
- ✅ **E4 更多 response code**（B2）：`arm_response(typ=,code=,text=)`（`[UNAVAILABLE]`/`[OVERQUOTA]`/任意）。
- ✅ **E5 通用畸形/損毀注入**（B3）：`arm_unsolicited`（非預期 untagged 行）+ 損毀 encoded-word 容錯。
- ✅ **E6 限流/連線上限**（B4）：`ImapServer(max_connections=N)` → `* BYE [UNAVAILABLE]`。
- ✅ **E7 非同步 untagged 推送**（C1）：`arm_async_expunge(uid, before_op=)`（推 EXPUNGE + 真實移除）。
- ✅ **E8 強制狀態機檢查**（C2）：`enforce_state=True` → 非法指令順序回 `BAD [CLIENTBUG]`。
- ✅ **E9 專屬遙測**（A3/A4/A5）：`transitions` + `assert_state_machine_legal()`、`fault_events`、
  `t_mono`/`injected_latency_s` + `timing_report()`，併入 `loop_report()`。
- ✅ **E10 通用序列對齊器**（B1）：`assert_sequence(expected, subsequence=)`。
- ✅ **E11 MIME 內文/附件建模**（DATA-4）：`mime_message`/`mime_server` + `BODY[]`/`BODY[TEXT]`/`RFC822`/
  `RFC822.SIZE`/`BODYSTRUCTURE`（皆對拍真 imaplib，含 multipart-alternative 含 CJK 與附件；
  `body-fld-octets`/`lines` 為**編碼後**計數）。

### 7.2 協定/網路擬真擴充（P1–P11，2026-06-25 第二批，SR 腦力激盪項目）

獨立 SR 腦力激盪揭示的「未模擬的真實 IMAP/網路病態與協定特性」，一次性補完（測試
`test_imap_server_extensions.py`，皆對拍真 imaplib）：

- ✅ **P1 TCP 分段/任意切塊讀取**：`SimIMAP4_SSL(chunk_size=N)` / `connected_client(chunk_size=)`——
  傳輸層 `read`/`readline` 以任意小塊重組（含 mid-literal）；chunk=1 下整流程仍正確。
  **誠實界定**：模擬的是「**對 imaplib reader 的交付分段**」（驗證讀取重組在任意切塊下正確）——
  位元組已同步緩衝於 `_inbuf`，**非真正的非同步 TCP 到達**（無「先到一半再 stall」）。
- ✅ **P2 `* n EXISTS` 成長通知**：`arm_exists(count, before_op=)`（信箱成長的非請求 untagged 通知）。
- ✅ **P3 session 中途 UIDVALIDITY 變更**：`set_uidvalidity(mailbox, value, reassign_uids=)`（信箱重建、
  舊 UID 失效——破壞性 App 最毒的「用過時 UID 搬錯信」bug 類）。
- ✅ **P4 多封/交錯 async EXPUNGE 重編序號**：`arm_async_expunge([uids], ...)`（逐封移除、序號逐封下移）。
- ✅ **P5 SEARCH 真條件**：`UID SEARCH` 解析 `SEEN/UNSEEN/DELETED/UNDELETED/FLAGGED/UNFLAGGED/
  FROM/TO/SUBJECT/HEADER/BODY/UID`（AND），取代恆 ALL。
- ✅ **P6 APPEND（同步 literal）**：`_cmd_append` + `feed_literal`/`expecting_literal`（傳輸層 literal 感知）
  + `LITERAL+`；新增郵件配新 UID、回 `[APPENDUID]`，可再被 SEARCH/FETCH 取回。
- ✅ **P7 範圍 FETCH `BODY[sec]<offset.length>`**：回應標 `<offset>`、literal 取對應切片。
- ✅ **P8 超大 literal（~1MB）round-trip**：驗證大內文/附件的精確讀回（記憶體壓力路徑）。
- ✅ **P9 greeting 變體**：`greeting_mode="preauth"`（`* PREAUTH`→AUTH）/`"no_caps"`（無 `[CAPABILITY]`→
  客端另送 CAPABILITY）/`"ok"`；連線期 BYE 見 `arm_connect_failure(mode="bye")`。
- ✅ **P10 畸形/亂序 tagged 行**：`arm_unsolicited` 注入非預期 tagged 行 → 真 imaplib **受控 abort**
  （非靜默誤判——證明協定健壯）。
- ✅ **P11 STATUS / NAMESPACE / LSUB + CONDSTORE**：新增三命令；`supports_condstore=True` → SELECT 報
  `[HIGHESTMODSEQ]`、FETCH 支援 `MODSEQ`。（COMPRESS/STARTTLS 因 stdlib imaplib 無對應、產品走 SSL+XOAUTH2，
  刻意不納入。）

---

## 8. 開發規範（Mandate — 與 CLAUDE.md §7 對齊）

**往後任何對接 `imaplib` 底層的開發，都必須由本 IMAP 模擬器引擎負責模擬所有可能情況——正常使用與
非正常情況皆然。** 具體：

- seam（`imap_client.py`）以下、任何跨真實 IMAP 協定的程式碼，其測試一律以**真 imaplib over `ImapServer`**
  驅動（`SimIMAP4_SSL` / `install_server` / `connected_client`），**禁止**手刻 imaplib 回應或重引退役的 FakeIMAPConn。
- 新增 IMAP 方法 → **先**在 `imaplib_probe` 加「對拍真 imaplib」的保真案例，**再**於引擎以真實伺服器行為擴充（§5 鐵則 2）。
- 正常路徑與異常路徑**都要**覆蓋：異常用 `arm_expiry(...)` 注入；若需要的情境落在 §7 規劃缺口，
  **先擴充引擎（連同保真案例）**，再寫產品測試——不得繞過引擎、不得臆造。
- 任何 bulk/loop 行為必走引擎並分析其 log（§3B）。

---

## 9. 參考

- 重打造過程計畫：`doc/imap-simulator-plan.md`
- 教訓（為何測請求/用引擎/斷言不變量）：`doc/lessons-learned.md`
- 開發鐵則（濃縮）：`CLAUDE.md §7`
- 憲法（Backend isolation / Safe-by-default / Secrets / 誠實 CHANGELOG）：`.specify/memory/constitution.md`
- imaplib 參考源（vendored v2.60，gitignored）：`imaplib/imaplib.py`；產品實跑為 stdlib
  `C:\Python312\Lib\imaplib.py`（3.12.x）。兩者僅傳輸內部不同、解析路徑相同，`SimIMAP4_SSL` 覆寫傳輸
  → 版本無關。回應格式有疑慮時**以此源或實跑確認，絕不臆測**。
- 對拍真解析器：`tests/imaplib_probe.py::ScriptedIMAP4`。
