# Phase 0 — Research & Decisions: Bulk Classify Efficiency (Phase 1)

無 `NEEDS CLARIFICATION`（spec 經 `/speckit.clarify` 已釘三項決策）。以下為實作取向決策。

## D1 — 存在性查詢採 `UID SEARCH ALL`（含已標 `\Deleted`）

- **Decision**: `list_uids` 以 `SELECT folder (readonly)` + `UID SEARCH ALL` 取得現存 UID 集合，語意涵蓋信箱中尚未 expunge 的所有郵件（含已標 `\Deleted`）。
- **Rationale**: 現行 `_list_headers_impl` 正是 `UID SEARCH ALL` 後再 FETCH（spec Clarify Q1：維持現況）。沿用 `SEARCH ALL` 保證 FR-003「逐列判定等價現況」，且這正是 P1 要消除的「FETCH 全表頭」之前那一步——直接回傳該步的 UID 集合即可。
- **Alternatives rejected**: `SEARCH UNDELETED`（會排除已標刪、改變既有判定 → 違 FR-003）；保留 `list_headers` 再取 `h.uid`（即現況浪費，違 P1 目的）。

## D2 — 新增後端中立方法 `list_uids`（而非旗標或衍生）

- **Decision**: `MailBackend` 協定新增 `list_uids(folder, *, on_progress=None) -> set[str]`；`OutlookIMAPClient` 與 `FakeBackend` 各自實作。分類 `_source_uids` 改呼叫之。
- **Rationale**: 跨 seam 只傳輕量領域識別（UID 字串集合），符合 Principle I；`list_headers`（功能1/2 需內容）原樣保留、零回歸；FakeBackend 實作極簡。
- **Alternatives rejected**: 給 `list_headers` 加 `uids_only=True` 旗標（混淆語意、回傳型別分歧、易誤用）；在 `imap_client` 外用 `list_headers` 衍生 UID（無法消除整夾 FETCH）。

## D3 — 單次查詢下的 determinate 進度（Clarify Q2）

- **Decision**: `list_uids` 接受 `on_progress`，取得 UID 集合後以「該夾郵件數」為總數驅動 determinate 進度至完成（`done→total`），沿用既有 `progress.reporter` 的同款狀態條；**不注入任何人工延遲**。
- **Rationale**: Clarify Q2 要保留逐項式 determinate 進度條、不退化為「無回饋」。`progress.reporter` 本身有 `0.1s` 重繪節流且最後一筆必繪（見 `progress.py`），故本就快的單次查詢會自然渲染為「狀態條出現並迅速填滿至 100%」。這保住 determinate bar 的 UX（FR-007、SC-007），同時對效能目標與 Principle VI 誠實——**不為了動畫而拖慢**。
- **誠實標註（須向使用者揭示）**: 因查詢已是單次往返、本就極快，狀態條會「很快填滿」而非肉眼可見的逐格爬動；這是真實速度，非缺陷。若日後要肉眼動畫，僅能靠人工延遲（不採，違效率/誠實）。
- **使用者確認（2026-06-28）**: 接受少次往返時狀態條快速衝至 100% 的現象、**不需人工延遲**；保留 `on_progress` 管線的用意在於**預留未來多次往返情境**（如 P5 重連續傳、超大信箱分批）時 progress 仍自然存在、不必再補管線。
- **Alternatives rejected**: 人工 `sleep` 製造爬動（拖慢、不誠實）；完全不顯示/只印標籤（退化為無回饋，違 Q2）。

## D4 — 候選分組 + 輸出還原 CSV 列序（P4 + Clarify Q3）

- **Decision**: `execute` 取 `candidates(items)`（CSV 列序）後，以 `(current_folder, target_folder)` 穩定排序得**處理順序**並保留各候選原索引；逐一處理（同夾相鄰、決定性）；最終回傳 `list[MoveResult]` 時依**原 CSV 索引**還原排序。
- **Rationale**: 同時滿足 FR-004（內部分組處理）、FR-005（結果集合不變）、FR-012/SC-008（使用者所見逐列結果依 CSV 列序）。`sorted` 為穩定排序，確保決定性（SC-004）。
- **早停語意**: 連續失敗達門檻提前停止（既有行為）改在「分組順序」下判斷——同夾失敗會相鄰群聚，早停語意保留；未處理者不在結果中（cli 既有「剩餘 N 筆」回報不變）。回傳仍只含已處理者、依 CSV 列序。
- **Alternatives rejected**: 處理與輸出皆用分組序（違 Q3，使用者核對不便）；只排序不留原索引（無法還原 CSV 序）。

## D5 — 與既有透明重連/有界重試相容（FR-011）

- **Decision**: `list_uids` 比照 `list_headers`/`move`，以 `self._with_reconnect(lambda: self._list_uids_impl(...))` 包裝；`_list_uids_impl` 含 `select`，重連後重跑整個查詢（唯讀、冪等、可安全重抓）。
- **Rationale**: 重用既有韌性機制（feature 005），零新增；唯讀查詢重抓無副作用。
- **驗證**: 模擬器 `arm_expiry(before_op="search"/"fetch", mode=...)` 注入中斷，斷言重連後 `list_uids` 仍回正確集合、不重複/不遺漏。

## D6 — 驗證與量測取向（測請求端 + log）

- **Decision**: 跨 seam 一律走 IMAP 模擬器引擎。關鍵斷言：
  - `server.loop_report()["fetches_per_folder"]`：分類**檢查報告**對來源夾的「整夾完整標頭 FETCH」= 0（改由 `UID SEARCH` 取代）。
  - `command_counts`：出現 `UID SEARCH`；分類報告階段無整夾 `UID FETCH`。
  - `bytes_*`：報告階段下傳量較現況大降（SC-003 ≥90%，以同情景前後對照）。
  - `assert_all_fetches_request_uid()`：既有 UID 不變量續守。
  - 分組：以 `command_log`/`SELECT`+`UID MOVE` 順序驗證同夾相鄰（搭配 P4，雖本期未改 move 路徑，分組順序仍可由執行序觀察）。
- **分類邏輯層**：以 `FakeBackend`（含 `list_uids`）驗判定等價、分組順序、結果 CSV 列序、deleted 視為存在。
- **Rationale**: CLAUDE.md §7 鐵則——測我方送出的請求、用引擎、驗輸出不變量；避免「測回應解析」的假覆蓋。
