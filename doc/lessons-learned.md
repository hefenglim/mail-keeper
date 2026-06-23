# MailKeeper — Lessons Learned / 工程注意事項 (PEM)

> Post-Event Memo。記錄真實使用中暴露的效能與韌性問題、根因、通用鐵則與審查清單，供日後開發與 Senior Review 對照。新事件往下追加。

---

## 2026-06-23 — 功能3 分類搬移：冗餘重抓導致 token 過期、連線連環失敗

### 事件 (Incident)
對 426 封 `Inbox` 標記 28 封搬到 `Inbox/TestMove`，執行後：

```
完成：成功搬移 2 / 28。
  失敗：164@Inbox … command: UID => Session invalidated - AccessTokenExpired
  失敗：205@Inbox … command: EXAMINE => socket error: EOF
  失敗：240@Inbox … socket error: EOF occurred in violation of protocol (_ssl.c:2427)
  …（其餘 25 筆同樣 EOF）
```
（觀測於 v0.4.0。）

### 根因 (Root Causes)
1. **O(n×m) 冗餘重抓（主因）**：`classifier.execute` 在每搬一封前都呼叫 `backend.list_headers(current_folder)` 重抓整個資料夾的全部標頭。28 筆 × 426 封 ≈ 12,000 次標頭讀取。log 中 `command: EXAMINE => socket error` 即每筆搬移都重跑 `list_headers`（內含 `select`/EXAMINE）的鐵證。
2. **無 token 重取／斷線重連（次因）**：操作被主因拖到數十秒～數分鐘，access token 中途過期 → `Session invalidated - AccessTokenExpired` → IMAP session 作廢 → SSL 連線被關 → 後續每個指令 `socket error: EOF`。`execute` 雖逐筆 catch 後續行，但連線已死、無重連，剩餘 25 筆全敗。
3. **選單未隔離單一動作錯誤（相關）**：互動模式下，找不到檔案等預期錯誤會一路冒到 `cli.main` 錯誤邊界 → `SystemExit` → 整個程式退出，而非回到選單。

### 通用鐵則 (The Rule)
> **處理大量資料的迴圈，絕不可在迴圈內重複做整批的網路抓取。先抓一次、快取，迴圈內只比對並隨進度更新。**

正確範式：`classifier.build_report` 的 `uid_cache`（每個來源資料夾只抓一次）。反例：`classifier.execute` 的逐筆重抓（待修）。

### 審查清單 (Audit Checklist) — 新增/審查任何功能前自問
- [ ] 這個迴圈裡有沒有可以移到迴圈外、只做一次的網路/批次操作？
- [ ] 複雜度是 O(n+m) 還是 O(n×m)？大量資料下會放大成幾千次往返嗎？
- [ ] 大量網路操作有沒有 token 過期／斷線的韌性？（偵測 `AccessTokenExpired`/EOF → 及早停止或重連 → 清楚提示，而非刷同樣錯誤 N 次）
- [ ] 互動（選單）模式下，單一動作失敗會不會把整個 app 帶走？
- [ ] 輸出類動作（匯出工作表、匯出資料夾）是否單趟、無巢狀重抓？

### 功能審查結果（2026-06-23, v0.5.0 程式碼）
| 功能 | 迴圈內網路行為 | 複雜度 | 評估 |
|------|----------------|--------|------|
| 功能1 `export_worksheet` | `list_headers` 一次 | O(m) | ✅ 乾淨 |
| 功能2 `export_folders` | `list_folders` 一次 | O(1) | ✅ 乾淨 |
| 功能3 `build_report` | 各夾 `list_headers` 經 `uid_cache` 快取 | O(夾數×m) 去重 | ✅ 乾淨（正確範式）|
| 功能3 `execute` | **每搬一封重抓整夾** | **O(n×m)** | ❌ 唯一元凶 |

### 行動項 (Action Items)
- **A**：`menu.run` 隔離單一動作錯誤 → 回選單、不退出。
- **B（最高槓桿）**：`execute` 迴圈外快取各來源夾 UID 集合、搬移後 `discard`；O(n×m) → O(n+m)。
- **C**：搬移端偵測 `AccessTokenExpired`/EOF → 及早停止 + 清楚提示；完整 token 重連列入 R7。
- **D**：補 `list_headers` 分批解析的離線測試、批次失敗可見性（不再靜默吞、不假性推進進度）。

### 版本歸屬 (0.4.0 vs 0.5.0)
- 真實異常（選單退出、`execute` 重抓）**自 v0.4.0（feature 003）既有**，feature 004 未改動該結構。
- v0.4.0 `list_headers` 為逐封 fetch（UID 直接取自 SEARCH，保證正確）；v0.5.0 改分批 fetch + 由 FETCH 回應取 UID。屬新增的較脆弱路徑。
  > **⚠ 更正（2026-06-23 後續）**：當時此處原寫「`UID FETCH` 回應依 RFC 3501 §6.4.8 必含 UID，實證匯出 UID 正確」——**錯誤**。實證來自 0.4.0 log（走逐封路徑），不適用於 0.5.0 的批次路徑。0.5.0 批次 FETCH **未顯式索取 UID**，Outlook 回應 metadata 不含 UID，導致匯出 UID 全空。詳見下方新事件。教訓：不要拿「不同程式路徑」的觀測去反證另一條路徑的風險。

### 邊界情境矩陣（分類搬移）— 必須安全處理
| 情境 | 期望行為 |
|------|---------|
| 重跑同一檔、部分已搬走 | 已搬走者來源 uid 不在 → 標「不可行」、不重搬、不崩潰（**操作須冪等**）|
| 報告後、執行前 uid 被搬走（TOCTOU）| `execute` 重查 → 該列「執行時已不存在」失敗 |
| 同 uid 出現兩次 / 一封對兩個目標 | 第一筆搬、其餘執行時失敗（不崩潰）|
| 來源夾讀取失敗（連線中斷/逾時）| **如實報錯**，不可遮蔽成「不可行」 |
| 建夾/搬移失敗 | 該列失敗、其餘續行；連續多筆失敗則提前停止 |
| 確認前 | 列出將新建的資料夾（揭露副作用）|

**原則：重跑必須安全冪等；別把暫時性錯誤（連線）偽裝成資料錯誤（不可行）；破壞性/有副作用動作在確認前要把後果攤開。**

---

## 2026-06-23 — 匯出工作表 UID 全空：批次 FETCH 未索取 UID（v0.5.0 致命回歸）

### 事件 (Incident)
v0.5.0 匯出 `Inbox` 工作表，每一列 `uid` 欄皆空白：
```
uid,current_folder,target_folder,date,from,to,subject
,Inbox,,"Tue, 6 Sep 2016 ...","\"Serena Yeh\" <...>",<kevin@outlook.my>,FW: ...
,Inbox,,...
```
工作表完全無法用於功能3（搬移依 `(current_folder, uid)`）——靜默產出無效檔。

### 根因 (Root Cause)
v0.5.0 將 `list_headers` 由「逐封 fetch」改為「批次 UID FETCH（每批 50）」。逐封時 UID 直接沿用 SEARCH 結果；批次後無法對位，改以正則從 FETCH 回應 metadata 解析 UID（`_extract_uid`）。**但 FETCH 的 data-items 寫成 `(BODY.PEEK[HEADER.FIELDS (...)])`，未顯式索取 `UID`**，Outlook 回應 metadata 不含 `UID <n>` → 每列解析為空字串。

### 修法 (Fix)
1. FETCH data-items 改為 `(UID BODY.PEEK[HEADER.FIELDS (...)])`（UID 置於 BODY 之前，確保進入帶 literal 的 metadata 段）。
2. **防線**：解析不到 UID 即 `raise BackendError` 中止，絕不靜默吐出缺 UID 的無效工作表（honest failure > silent corruption）。

### 通用鐵則 (The Rule)
> **跨越後端 seam 的每個欄位，都必須由「我方明確索取」的資料推導，不可仰賴伺服器「應該會附帶」的隱含行為。** 批次化／改寫資料存取時，逐一確認每個輸出欄位的來源是否仍成立。

> **測試 fixture 必須模型化「真實後端的實際行為」，不可植入「我方假設它會回傳」的資料。** 本 bug 被一條測試遮蔽：它在假 FETCH 回應裡自行塞了 `UID 10`/`UID 11`，於是綠燈，但真實 Outlook 在未索取時根本不回 UID。**離線假後端要重現真實的「不給」，而非理想的「會給」。** 已新增「FETCH data-items 必含 UID」的協定層回歸守衛，把契約釘死在請求端而非回應端。

### 審查清單增補
- [ ] 改寫資料存取（逐封→批次、欄位重構）後，每個輸出欄位的來源是否仍由「明確請求」保證？
- [ ] 假後端 fixture 重現的是真實後端「會回什麼」，還是我方「希望它回什麼」？兩者不同即為盲區。
- [ ] 對「靜默產出空/缺欄位」是否有防線（寧可大聲中止，不要默默吐無效檔）？
- [ ] 不要用「另一條程式路徑」的觀測去反證某條路徑的風險（見上方 0.4.0 log 反證之誤）。

---

## 2026-06-24 — 深度檢討：為何測試沒抓到 + 可信度系統性補強

### 觸發
使用者質疑：這麼多測試為何一條都沒抓到 UID 全空？其他測試還能不能信？盤查後的誠實結論與補強。

### 為何沒抓到（精確機制）
測試 `test_list_headers_parses_batched_fetch_uids` 是**套套邏輯**：fixture 自己在假回應裡塞了 `UID 10`，再斷言解析得出 `10`——它測「解析」，但 bug 在「我們沒去要 UID」。三個疊加成因：(1) 寫 mock 與寫 code 同一人、同一錯誤心智模型，mock 無法反證 code；(2) 測錯 seam——測回應端而非請求端；(3) 無任何端到端輸出不變量（「每列必有 uid」）。

### 盤查中當場挖到的第二顆地雷（資料遺失級）
`imap_client.move` 後備路徑（伺服器無 `UID MOVE` 時）：**未檢查 COPY 就標刪+整夾 EXPUNGE**。後果：COPY 失敗仍刪除（無複本即刪）、整夾 EXPUNGE 波及他人已標 `\Deleted` 的郵件。此路徑**先前零測試**（FakeBackend 的 move 是 in-memory 改 dict，抓不到協定行為）。已修：COPY 成功才標刪、改 `UID EXPUNGE` 限定該封。

### 結構性根因
架構刻意讓後端可替換、到處注入 FakeBackend——利於離線測邏輯，卻造成**單一文化**：95% 測試在 seam 以上，唯一不可替身、真接協定的 `imap_client.py` 反而測得最少、最差。讓設計可測的機制，把全部真實風險擠進了唯一測不到的模組。

### 系統性補強（已落地）
| 措施 | 檔案 | 擋住什麼 |
|------|------|---------|
| 忠實 IMAP 模擬器（真狀態機 + 指令日誌 + 只回索取的 items） | `tests/imap_sim.py` | fixture 再也無法捏造「沒索取的欄位」 |
| 模擬器自我驗證 | `tests/test_imap_sim.py` | 確保「被信任者」本身保真 |
| 契約測試（測請求端 + 輸出不變量 + 破壞性動作安全 + XOAUTH2 格式） | `tests/test_imap_contract.py` | UID / move / auth 這類 seam bug |
| CI 持續測試 + 覆蓋率閘門（`imap_client` ≥88%） | `.github/workflows/ci.yml` | 每次 push/PR 把關，盲區量化 |
| 發版前真實帳號 smoke | `doc/release-smoke.md` | 唯一碰真伺服器的關卡 |
| 突變測試 | `scripts/mutation.ps1` | 把「測試可不可信」變成數字 |
| 紀律固化 | `CLAUDE.md §7/§4` | 跨 seam 程式：測請求、用模擬器、驗不變量 |

### 鐵則（最終版）
> **跨越後端 seam 的每個輸出欄位，都必須由「我方明確索取」保證；測試要測「我們送出的指令」而非只測「怎麼解析回應」；假後端要重現真實伺服器「會回什麼」，不是我方「希望它回什麼」。破壞性動作（刪除）先確認有複本、且把波及範圍限到最小（`UID EXPUNGE`）。寧可大聲中止，不要靜默產出無效資料。**

### 可信度誠實結論
- 純函式與上層邏輯（organizer/classifier/cli/csv/progress/config）測試**是真的在測真邏輯**，可信——但都建立在「後端正確」的前提上。
- 風險集中於 `imap_client.py` 真實協定路徑。本輪把 UID、`move` 兩條補上測試與防線、建立模擬器與 CI 閘門後，這塊從「靠運氣」轉為「有結構保證」。仍不保證零 bug，但已可量測、可回歸。

---

## 2026-06-24 — 模擬器升級為離線測試的堅實地基（保真度 + 母版資料集 + 雙層驗證）

### 緣由
`FakeIMAPConn` 將成為日後所有功能的離線測試依據，必須與真實伺服器**位元組級**一致，否則地基是沙子。

### 規格用「真的」確認，不靠猜（關鍵發現）
以 `tests/imaplib_probe.py::ScriptedIMAP4` 把 RFC 3501 wire bytes 餵進**產品實際使用的 imaplib 解析迴圈**，取得權威結構，再要求模擬器逐位元組相同（`tests/test_imap_fidelity.py`）：
- 確認 FETCH 的 `tuple[0]` 是 `b'<seq> (<items> {len}'`、**不含 `FETCH`**（我原本不確定 → 拿真的跑才敢定案）。
- **挖到並修正一個保真缺口**：LIST 回應的 CJK 夾名真實上是 **modified-UTF-7**（`&U,BTFw-`），模擬器原用裸 UTF-8。已補 `_encode_mutf7`（產品 `_decode_mutf7` 的逆運算），並加端到端 round-trip 測試。

### 母版資料集（copy-per-test）
`tests/imap_dataset.py::master_mailboxes()` 涵蓋 ASCII/CJK/emoji/encoded-word/已讀/使用者已標刪/空主旨/超長主旨/巢狀夾/CJK 夾名；`fresh_sim()` 每次深拷貝一份獨立資料，測試互不汙染。新情境就擴充母版。

### 雙層驗證（最高可信度）
測試後同時斷言：(1) **指令動作日誌** `sim.log`/`uid_commands()`——送出的 IMAP 指令、參數、順序符合規格且安全；(2) **資料狀態** `sim.snapshot()` 前後比對——變動合理、且**別人沒被波及**（如他人 `\Deleted` 郵件不被連坐 expunge）。範式見 `tests/test_imap_dataset.py`。

### 鐵則
> **模擬器的每一種回應都必須先用真 imaplib 對拍確認、再寫進保真度測試；新增 IMAP 功能 = 先加一條 fidelity case。測試從母版複製出發，跑完做雙層確認（指令日誌 + 資料快照差異）。** 已寫入 `CLAUDE.md §7`。
