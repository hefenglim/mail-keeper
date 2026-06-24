# Quickstart / Validation: Bulk Resilience (R7)

證明本功能端到端可用的可執行驗證情境。全程**離線**，以擴充後的 `tests/imap_sim.py::FakeIMAPConn` 模擬 token 過期/連線中斷/重連恢復；雙層驗證（指令日誌 + 狀態快照）。細節見 [data-model.md](./data-model.md) 與 [contracts/](./contracts/)。

## 前置

```powershell
$env:PYTHONPATH = "src"
python -m pytest tests/ -q          # 全套綠
python -m mypy src/mailkeeper       # 乾淨
pwsh scripts/coverage.ps1           # 套件 ≥85%、imap_client ≥88%
```

## 場景 1（P1）— token 中途過期，分類仍全完成（核心）

- **Given**：母版 `fresh_sim()` 的 INBOX 有多筆待搬移；模擬器設定為「第 N 筆 move 擲 `AccessTokenExpired`、之後可由注入的 token 提供者靜默續期恢復」。
- **When**：以真實 `OutlookIMAPClient`（注入靜默 token 提供者）跑分類 `--run`，全程不人工介入。
- **Then（雙層）**：
  - 指令日誌出現「偵測→`authenticate`（續期重認證）→重新 `select`→重試該 move」序列，且重連次數有界。
  - 狀態快照：所有候選最終搬移完成、**0 重複、0 遺漏**；他人 `\Deleted`（母版 uid 106）未被波及。
- **對映**：FR-001/002/003、SC-001/SC-002。

## 場景 2（P1）— 靜默續期不可行 → 乾淨停止

- **Given**：注入的 token 提供者擲 `ReauthRequired`（模擬 refresh token 失效）。
- **When**：分類 `--run` 進行中觸發。
- **Then**：操作乾淨停止；cli 印「需重新登入」訊息 + **正確的已完成/未完成數量**；退出碼非零；無 traceback、無 secret。重跑同一份工作表，已搬走者落入 `gone`/不可行、不重搬（冪等）。
- **對映**：FR-004、SC-004、SC-005。

## 場景 3（P2）— 同一分類流程整夾只讀一次

- **Given**：一份指向單一來源夾的工作表。
- **When**：完整跑「檢查報告 → 確認 → 執行」。
- **Then**：指令日誌中該來源夾的整夾標頭讀取（select+search+fetch 一輪）**只出現一次**；報告階段仍能標出不存在的郵件。
- **對映**：FR-007、SC-003。

## 場景 4（P3）— 暫時性抖動被退避重試吸收 + 門檻可設定

- **Given**：模擬器設「某筆 move 短暫失敗一次後即恢復」。
- **When**：跑分類 `--run`。
- **Then**：該筆最終成功；不因單次抖動觸發整體放棄。
- **再 Given**：`config.json` 設 `max_consecutive_failures: 5`。
- **Then**：連續失敗達 5 才停止（行為隨設定改變）；設定無效時用安全預設、不崩潰。
- **對映**：FR-005/006/008、US3 場景。

## 場景 5 — 匯出（功能1）中途斷線後整批重抓

- **Given**：模擬器設「標頭下載中途擲 EOF、重連後恢復」。
- **When**：跑 `export-worksheet`。
- **Then**：重連後整批重抓，輸出完整、**每列 UID 非空**（沿用 0.5.1 不變量）。
- **對映**：FR-001、edge case「匯出長時間下載中斷」。

## 真實帳號 smoke（發版前）

離線無法取代真實伺服器；發版前依 [`doc/release-smoke.md`](../../doc/release-smoke.md) 跑一次，並**新增一項**：對一個大資料夾刻意讓操作橫跨 token 壽命（或手動使 token 失效），確認自動恢復或乾淨停止符合預期。
