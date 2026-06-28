# Changelog

## [0.6.2] - 2026-06-29
### Changed — 大量分類搬移效能（feature 007，P4 分組 + P3 免重 SELECT + P2 批次 MOVE）
- **分類搬移批次化**：候選依 (來源夾,目標夾) 穩定分組，同群以 `UID MOVE <set>` 批次搬移（超過 `MOVE_BATCH_MAX=200` 分塊），批次失敗退逐封以精確歸因；500 封同夾搬移的搬移往返由「每封一次」降為「每批一次」（N→⌈N/批⌉）。
- **免重複 SELECT**：`OutlookIMAPClient` 追蹤目前選取的 `(資料夾, 讀寫模式)`，同夾連續操作不重選（連線/重連重置）；同夾搬移迴圈的多餘 SELECT 由 N→0。
- 新增**後端中立** `MailBackend.move_many`（批次搬移，回 `{uid: None/錯誤}`）；分類結果依**原 CSV 工作表列序**呈現（內部分組處理）。早停改**連線層級**：單列資料失敗不再提前停止（移除連續失敗計數）；`max_consecutive_failures` 保留為 inert/deprecated。
### Fixed
- **後備搬移冪等（backlog C1）**：伺服器不支援 `UID MOVE` 的後備路徑（copy→標刪→UID EXPUNGE），於「COPY 成功後、清除前」斷線重試**不再產生重複複本**——重試前以目標夾 `Message-ID` 去重（涵蓋「copy 後/標刪前」與「標刪後/expunge 前」兩窗口）；來源仍正確移除、他人已標 `\Deleted` 郵件不被波及。
### Tests
- 跨 seam 走 IMAP 模擬器引擎：擴充 `_uid_move` 支援 UID 集合、母版郵件帶 `Message-ID`、`HEADER Message-ID` 搜尋保真（對拍真 imaplib）；驗 `redundant_selects()==0`、批次 `UID MOVE` 計數、後備冪等（snapshot 目標複本==1）、重連 0 重複/0 遺漏、他人 `\Deleted` 不被波及。271 passed，全程離線、雙層驗證。

## [0.6.1] - 2026-06-28
### Changed — 大量信箱分類效能（feature 006，P1 存在性檢查最小化）
- **分類「檢查報告」不再整夾抓標頭**：判斷來源郵件是否存在，改為只取「來源夾現存 UID 集合」（一次 `UID SEARCH ALL`），取代原本下載並解析整夾完整標頭再幾乎全丟的浪費。實測 10,000 封來源夾的報告階段：~200 批 FETCH → **1 次 SEARCH**、下傳 ~1.63 MB → 僅 UID 清單（降幅 **≥90%**）；逐列判定與搬移結果**等價現況**。「現存」沿用 `UID SEARCH ALL` 語意（含已標 `\Deleted` 未 expunge 者）。
- 新增**後端中立**的 `MailBackend.list_uids(folder)`（IMAP 實作＝`SELECT readonly` + `UID SEARCH ALL`，僅置於 `imap_client.py`；以 `_with_reconnect` 包裝、與透明重連相容）；`classifier._source_uids` 改用之。需要內容的功能（匯出工作表、列出標題）與分類**搬移執行路徑不變**。
### Tests
- 跨 seam 走 IMAP 模擬器引擎驗請求端：`list_uids` 只送 `UID SEARCH`、零整夾 `UID FETCH`、含已標刪 UID、重連後完整回傳、下載量較 `list_headers` 降 ≥90%；分類層以 `FakeBackend` 驗判定等價／每夾一次／進度透傳。260 passed（+1 xfailed），全程離線、雙層驗證。
### Notes
- 本期**不含** P4 候選分組（延至 P2/P3 同期，理由見 `doc/mailkeeper-performance-report-20260627.html` 與 `specs/006-bulk-classify-efficiency`）。

## [0.6.0] - 2026-06-24
### Added — 大量信箱的效能與韌性（feature 005, R7）
- **token 過期 / 連線中斷自動恢復**：操作中途偵測到 session 失效/EOF 時，後端**透明重連**（沿用既有授權**靜默續期** `auth.get_token_silent` → 重建 IMAP 連線 → 重新認證 → 有界退避重試），分類**項目級續做**、匯出**整批重抓**，直到完成。靜默續期不可行（refresh token 失效）→ 擲後端中立的 `ReauthRequired` → cli **乾淨停止**並回報已完成/未完成數（重新登入後以同一份工作表重跑續完，冪等）。重連/續期/重試屬協定細節，僅置於 `imap_client.py`；MSAL 僅靜默路徑在 `auth.py`；client 以注入式 `token_provider`/`on_status` 參與，維持後端隔離、**不新增 runtime 相依**。
- **韌性設定可調**：`config.json` 可選 `max_consecutive_failures`/`max_reconnect_attempts`/`max_retries_per_op`/`backoff_base_seconds`/`backoff_cap_seconds`（無效/缺漏 → 安全預設、不崩潰）。
### Changed
- **同一分類流程整夾標頭只讀一次**：`classifier` 引入共用 `ClassifyCache`，「檢查報告」所讀為權威、`execute` 重用、**不再二次整夾掃描**（來源夾讀取 2→1）；TOCTOU 由搬移動作安全失敗兜（冪等）。`build_report`/`new_folders`/`execute` 新增可選 `cache` 參數（向後相容）。
- **進度狀態條依迴圈性質啟動**：**網路 in/out 迴圈一律顯示**（不設件數門檻），純 CPU 迴圈維持 >30 才顯示（`progress.reporter(network=...)`）。恢復/重連期間有 `on_status` 狀態提示（編碼安全 stderr，永不含 token）。
### Tests
- 模擬器升級擬真：`FakeIMAPConn` 支援 token 過期/EOF 注入（`arm_expiry`，含 `persist`）、session 於重新認證後恢復、`connected_client` 走真實 client 重連路徑。新增「日誌效率斷言」（整夾只讀一次、list 只一次）抓冗餘。187 tests，全程離線、對拍真 imaplib、雙層驗證。

## [0.5.1] - 2026-06-24
### Fixed
- **致命：匯出工作表 UID 全空（0.5.0 回歸）**。0.5.0 將 `list_headers` 改為分批 UID FETCH 後，FETCH 的 data-items 未顯式索取 `UID`，Outlook 回應 metadata 不含 `UID <n>`，導致每列 `uid` 解析為空字串——匯出的工作表完全無法用於功能3 分類（搬移依 `(current_folder, uid)`）。修法：FETCH 改為 `(UID BODY.PEEK[HEADER.FIELDS (...)])`（UID 置於 BODY 之前）。並加防線：若仍解析不到 UID 即大聲報錯中止，絕不靜默產出缺 UID 的無效工作表。
  > 0.4.0 逐封 FETCH 時 UID 直接沿用 SEARCH 結果故無此問題；批次化才暴露。先前測試 fixture 在假回應中自行塞入 `UID`，與真實後端行為不符而遮蔽了此 bug（已修正測試以模型化真實情境，並新增「FETCH 必含 UID」回歸守衛）。見 `doc/lessons-learned.md`。
- **資料遺失防護：`imap_client.move` 後備路徑**。伺服器不支援 `UID MOVE` 時的後備 copy→標刪→expunge 有兩個資料遺失風險：(1) **未檢查 COPY 結果就標刪+expunge**（COPY 失敗則郵件沒複本就被刪）；(2) 用**整夾 `EXPUNGE`** 會波及信箱內其他已標 `\Deleted` 的郵件。修法：COPY 成功才標刪；刪除改用 **`UID EXPUNGE`（RFC 4315 UIDPLUS）只清該封**，僅在伺服器無 UIDPLUS 時才退回整夾 EXPUNGE。此路徑先前**零測試**。
### Changed
- **功能3 初步檢驗顯示進度**：`classifier.build_report` 讀取各來源夾標頭時接上進度回呼（先前無提示，大資料夾像當機）。`execute` 首次讀來源夾現存 UID 時亦同。兩者新增可選 `progress` 工廠參數（向後相容）。
- **進度條視覺化**：`progress` 模組除 `done/total (%)` 文字外，加上 ASCII 方塊狀態條（`[██████░░░░] 213/426 (50%)`），完成比例一目了然。
- **主選單開頭顯示版本與 build 時間**：`=== MailKeeper v0.5.1｜build YYYYMMDD-HHMMSS ===`。build 時間於建置時由 `scripts/stamp-build.py` 烙印進 wheel（`buildinfo.build_stamp()`；dev/editable 安裝則回退為套件檔案 mtime）。`menu.run` 新增 `header` 參數。
### Added — 測試可信度基礎建設（回應「為何測試沒抓到」之檢討）
- **忠實 IMAP 模擬器 `tests/imap_sim.py::FakeIMAPConn`**：1:1 對齊 `imaplib` 介面與回應資料結構、真狀態機底層、完整 IMAP 指令動作日誌；**只回傳有索取的 data items**、EXPUNGE 忠實波及全部 `\Deleted`、COPY/MOVE 目標不存在回 `NO`。配 `tests/test_imap_sim.py`（鎖定模擬器自身保真度）。
- **契約測試 `tests/test_imap_contract.py`**：對模擬器執行 `OutlookIMAPClient`，查核送出的 IMAP 指令正確安全 + 回應解析正確 + 破壞性動作不遺失資料 + XOAUTH2 認證字串格式。本批測試本可在 0.5.0 即攔下 UID bug。
- **CI 工作流 `.github/workflows/ci.yml`**：每次 push/PR 離線跑測試 + mypy + 覆蓋率閘門（套件 ≥85%、`imap_client` ≥88%）。先前僅有 release 流程、無持續測試把關。
- 開發紀律固化：`CLAUDE.md §7/§4`（seam 契約測試規則）、`doc/release-smoke.md`（發版前真實帳號 smoke 檢查表）、`scripts/coverage.ps1`、`scripts/mutation.ps1`（突變測試）。
- **所有 Outlook IMAP 連線測試統一走 `FakeIMAPConn`**：移除各處零散假連線（`_FakeConn`/`FakeIMAP`/內嵌 `FakeSSL`/假 client），`connect()`/逾時/認證/cli 設定流程一律以 `imap_sim.install()` 把 `imaplib.IMAP4_SSL` 換成模擬器、跑**真實** `OutlookIMAPClient`。
- **模擬器升級為離線測試地基（保真度 + 母版資料集 + 雙層驗證）**：
  - **位元組級保真**：`tests/imaplib_probe.py` 把 RFC 3501 wire bytes 餵進真 imaplib 解析器取得權威結構，`tests/test_imap_fidelity.py` 斷言 `FakeIMAPConn` 與其逐位元組相同（FETCH/SEARCH/LIST）。藉此**修正一個保真缺口**：LIST 的 CJK 夾名改為 modified-UTF-7（`_encode_mutf7`，與產品 `_decode_mutf7` round-trip）。
  - **母版資料集** `tests/imap_dataset.py`：涵蓋 ASCII/CJK/emoji/encoded-word/已讀/使用者已標刪/空·超長主旨/巢狀·CJK 夾名；`fresh_sim()` 每測試深拷貝獨立一份。
  - **雙層驗證**：`sim.snapshot()` 提供前後資料狀態比對；測試同時查核指令動作日誌（`sim.log`）與資料變動是否合理（範式見 `tests/test_imap_dataset.py`）。
  - 新增 IMAP 方法時：先用真 imaplib 對拍加 fidelity case、於模擬器底層補上真實行為（已寫入 `CLAUDE.md §7`）。測試數 76→166。

## [0.5.0] - 2026-06-23
### Added
- 進度模組 `progress.py`：大迴圈（標頭讀取、分類搬移）於待處理項目數 > 30 且互動 TTY 時即時顯示進度（`\r` 就地更新），避免大資料夾誤判當機；非互動降級、不污染資料輸出、錯誤乾淨收尾、永不崩潰。
### Changed
- CSV 改用 **UTF-8 + BOM（utf-8-sig）** 讀寫：Microsoft Excel 直接正確顯示中文等多國語文（不再亂碼）；讀取容忍有無 BOM。
- 檔名輸入未填副檔名時自動補 `.csv`（`csv_io.ensure_csv_suffix`），確認訊息顯示補完後的實際檔名。
- `imap_client.list_headers` 改為**分批 UID FETCH**（每批 50，較逐封更快且可顯示進度）並接受後端中立 `on_progress` 回呼；`classifier.execute` 同；`MailBackend.list_headers` 以可選 keyword 參數擴充（向後相容）。
### Fixed
- **分類搬移去除冗餘重抓**：`classifier.execute` 不再每搬一封就重抓整個資料夾標頭（O(n×m) → O(n+m)，來源夾只抓一次並隨搬移更新），避免大量搬移拖到 access token 中途過期、連線 EOF 連環失敗（見 `doc/lessons-learned.md`）。
- **互動選單錯誤隔離**：單一動作失敗（如找不到 CSV 檔）改為印訊息後回選單，不再讓整個程式退出。
- **搬移韌性**：連續多筆失敗（疑似連線中斷）時提前停止並提示剩餘筆數，不再對死連線狂試。
- **確認前揭露副作用**：檢查報告列出「將新建的資料夾」清單。
- **分批 FETCH 失敗不再靜默吞**：批次失敗改為報錯（避免回傳不完整標頭誤導分類），並補上分批回應與 UID 解析的離線測試。
- **報告階段不遮蔽連線錯誤**：`build_report` 不再吞掉來源夾讀取失敗（連線中斷/逾時），改為如實往外傳，避免把所有列誤標為「不可行」（連線問題 ≠ 資料問題）。重跑同一檔時，已搬走的郵件正確標為不可行、不會重複搬移（操作冪等）。

## [0.4.0] - 2026-06-22
### Added
- 啟動互動選單 + 三個 CSV 子指令：`export-worksheet`（選資料夾 → 匯出分類工作表）、`export-folders`（匯出資料夾清單）、`classify`（依工作表 → 檢查報告 → 確認 → 搬移）。
- `MailBackend` 擴充：`list_folders()`、`list_headers(folder)`；`imap_client` 加資料夾列舉（含 modified-UTF-7 解析）與 `TO` 標頭。
- `MailHeader` 新增 `recipients`（向後相容，附加在最後）。
- 新模組 `csv_io`（固定英文表頭 `uid,current_folder,target_folder,date,from,to,subject`、UTF-8、標準跳脫）、`classifier`（dry-run 檢查報告 + 確認後搬移、來源 UID 失效逐列回報）、`menu`。
### Changed
- `cli` 改為 argparse 子指令 + 無參數時進選單；非互動安全（印用法 + 非零結束、不卡死）。功能3 破壞性動作預設 dry-run，`--run` 或互動確認後才搬移。
### Notes
- 本期為「手動流程」：CSV 由人＋AI 在工具外編輯；LLM 底層自動串接三功能屬未來階段。

## [0.3.0] - 2026-06-21
### Added
- 設定外部化：`config.json`（執行工作目錄，與 `token_cache.bin` 同處）提供 `client_id`、`email`，及可選 `imap_host`/`imap_port`/`timeout` 覆寫；`authority`/`scopes` 仍鎖在程式碼。
- 首次執行自動產生帶 `_README`/`_help_url` 的 `config.json` 範本並指示填寫；未填妥前以非零碼結束、不嘗試登入。
- 未填/佔位/壞 JSON 在登入前清楚攔截（指出欄位與檔案路徑）。
- 帳號不一致驗證：登入帳號與設定 email 不同時互動提問（用登入帳號〔可回寫〕／保留／中止），非互動則安全中止。
### Changed
- `auth.get_access_token(cfg)` 改吃有效設定並回傳 `(token, 已認證 email)`。
- `OutlookIMAPClient` 可接受 `host`/`port`/`timeout`（預設沿用程式碼值）。
- `config.py` 由「使用者編輯點」改為純程式碼預設；使用者改編輯 `config.json`。
### Security
- `config.json` 納入 `.gitignore`（使用者專屬；token 仍只存於 `token_cache.bin`，絕不寫入 config.json）。

## [0.2.0] - 2026-06-21
### Added
- 防崩潰輸出層 `console.py`：啟動將 stdout/stderr 重設為 UTF-8 並以安全寫入器包覆；任何主機/語系或重導向下都不會因編碼崩潰，無法表示的字元以 backslashreplace 佔位。
- IMAP 連線逾時 `IMAP_TIMEOUT`（預設 60 秒），伺服器無回應時快速失敗而非無限卡住。
- device code 登入等待上界為「裝置代碼有效期」（由 Microsoft 端決定、通常約 15 分鐘），目前不可調；silent refresh 路徑不受影響。
- 測試套件（離線）：`_decode()` 語料、輸出安全、CLI 錯誤邊界、IMAP 逾時。
### Changed
- `_decode()` 強化：攤平折疊標題、逐段解碼、宣告字集失敗改用 `charset-normalizer` 偵測回復；永不拋例外、永遠回傳字串。
- `cli.main()` 頂層錯誤邊界：已知失敗（認證/IMAP/網路/逾時）顯示簡潔訊息並以非零碼結束，不噴 traceback、不外洩 token。
### Dependencies
- 新增 `charset-normalizer`（標題字集偵測）。

## [0.1.0] - 2026-06-20
### Added
- 初版打包為正式 Python package（src layout，可 pip 安裝）
- Outlook.com IMAP 透過 OAuth2 / XOAUTH2 登入（auth.py，MSAL device code flow + token 快取）
- IMAP 操作隔離模組 imap_client.py：列出收件匣標題、搬移、標記已讀、加旗標
- 規則式郵件整理引擎 organizer.py，依賴 MailBackend 抽象介面（底層可替換）
- CLI 進入點：`mailkeeper` 指令與 `python -m mailkeeper`
