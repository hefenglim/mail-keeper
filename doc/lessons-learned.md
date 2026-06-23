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
- v0.4.0 `list_headers` 為逐封 fetch（UID 直接取自 SEARCH，保證正確）；v0.5.0 改分批 fetch + 由 FETCH 回應取 UID（`UID FETCH` 回應依 RFC 3501 §6.4.8 必含 UID，實證匯出 UID 正確）。屬新增的較脆弱路徑，非功能破壞，但須補測試與失敗可見性。

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
