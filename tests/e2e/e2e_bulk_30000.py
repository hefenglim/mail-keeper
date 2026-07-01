"""全端 E2E 驗收 —— 真 OutlookIMAPClient + classifier + cli 跑在線級 IMAP 模擬器引擎上。

規模：INBOX 30,000 封（含 ASCII / CJK / emoji / encoded-word 顯示名 / 已讀 / 使用者已標刪 /
空主旨 / 超長主旨），搬移 3,000 封。涵蓋資料各種狀態 + 模擬器各種狀態（happy / token 過期透明
重連 / UIDVALIDITY 變更信箱重建 / 搬移中途重連冪等 / 多目標夾 mUTF-7+引號 / drop_uid 與
fail_fetch 異常伺服器的「大聲失敗」防線）。

每個場景結束後：(1) 以引擎結構化命令 log 分析操作 trace 是否符合預期（命令次數/順序/冗餘/UID
不變量），(2) 以 snapshot 雙層驗證資料變動正確（他人 \\Deleted 不被波及、無重複複本）。所有
trace log 寫到專案目錄 e2e-trace-logs/ 供查閱。

非 pytest 測試（檔名無 test_ 前綴，不入 CI）；以 `python tests/e2e/e2e_bulk_30000.py` 直接執行。
所有 token 皆為明確假值（"E2E-FAKE-TOKEN-NOT-A-SECRET"），wire transcript 不含任何真實機密。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

for _stream in (sys.stdout, sys.stderr):  # Windows 主控台 cp1252 → 轉 utf-8 才能印 CJK
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from _pytest.monkeypatch import MonkeyPatch  # noqa: E402

from imap_server import ImapServer  # noqa: E402
from imap_sim import DELETED, SEEN, message  # noqa: E402
from imap_transport import connected_client  # noqa: E402

from mailkeeper import classifier  # noqa: E402
from mailkeeper.csv_io import ClassificationRow  # noqa: E402

FAKE_TOKEN = "E2E-FAKE-TOKEN-NOT-A-SECRET"
OUT_DIR = os.path.join(ROOT, "e2e-trace-logs")
BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines")
UPDATE_BASELINES = False  # --update：有意改動時重新祝福基準；否則預設比對、任何漂移即 FAIL

N = 30_000                       # INBOX 郵件總數
MOVE_N = 3_000                   # 搬移總數
UID_BASE = 1000                  # uid 自 1000 連續
MOVE_UIDS = [UID_BASE + i for i in range(MOVE_N)]   # 1000..3999
USER_DELETED_UIDS = set(range(UID_BASE + N - 50, UID_BASE + N))  # 末 50 封使用者標刪（在搬移範圍外）

# 已知特殊封（驗證 encoded-word / emoji / 空主旨 / 超長主旨 的解碼逐字等價）。皆落在搬移範圍。
SPECIAL = {
    UID_BASE + 0: ("週報 Q1 報告", "王經理 <boss@x.com>"),
    UID_BASE + 1: ("🎉 Happy New Year 新年快樂", "friend@x.com"),
    UID_BASE + 2: ("FW: 推薦職務", '"Serena Yeh" <serena@etalent.com.tw>'),
    UID_BASE + 3: ("", "c@x.com"),
    UID_BASE + 4: ("L" * 200, "d@x.com"),
}


def build_inbox(n: int = N) -> list:
    """建構 n 封多狀態 INBOX 郵件（每次全新物件）。"""
    msgs = []
    for i in range(n):
        uid = UID_BASE + i
        if uid in SPECIAL:
            subj, sender = SPECIAL[uid]
            msgs.append(message(uid, subj, sender, "me@outlook.my", "Mon, 1 Jan 2026"))
            continue
        flags = set()
        if uid in USER_DELETED_UIDS:
            flags.add(DELETED)            # 使用者自己標刪：搬移絕不可波及
        elif i % 7 == 0:
            flags.add(SEEN)               # 部分已讀
        msgs.append(
            message(uid, f"Bulk message {i}", f"sender{i}@x.com", "me@outlook.my",
                    "Mon, 1 Jan 2026", flags=(flags or None))
        )
    return msgs


def fresh_30k_server(**opts) -> ImapServer:
    """30,000 封 INBOX + 常見目標夾（Archive 空 / 台北 CJK / Junk Email 含空白 / Work 巢狀）。"""
    mailboxes = {
        "INBOX": build_inbox(),
        "Archive": [],
        "台北": [message(401, "在地通知", "local@x.com", "me@outlook.my", "Thu")],
        "Junk Email": [],
        "Work/Projects": [message(301, "Project kickoff", "pm@x.com", "me@outlook.my", "Wed")],
    }
    return ImapServer(mailboxes, **opts)


# ── trace log 輸出 ───────────────────────────────────────────────────────────

def _fmt_op(op) -> str:
    code = f" [{op.response_code}]" if op.response_code else ""
    mb = f" mbox={op.mailbox}" if op.mailbox else ""
    naff = len(op.affected_uids)
    aff = f" affected={naff}" if naff else ""
    lat = f" +{op.injected_latency_s:g}s" if op.injected_latency_s else ""
    return (f"#{op.seq:>6} {op.tag:<7} {op.command:<13} -> {op.result_typ}{code}"
            f"{mb}{aff}{lat}  [{op.state_before}->{op.state_after}]")


def write_logs(tag: str, server: ImapServer, *, extra: dict | None = None) -> list[str]:
    os.makedirs(OUT_DIR, exist_ok=True)
    written = []

    # 1) 結構化命令 trace（完整；這是「底層操作記錄」的主檔）
    cmd_path = os.path.join(OUT_DIR, f"{tag}.commands.log")
    with open(cmd_path, "w", encoding="utf-8") as f:
        f.write(f"# {tag} —— IMAP 模擬器引擎結構化命令 trace（共 {len(server.log)} 道）\n")
        f.write("# 欄位：#seq tag command -> result [code] mbox affected=影響封數 +注入延遲 [狀態轉移]\n\n")
        for op in server.log:
            f.write(_fmt_op(op) + "\n")
    written.append(cmd_path)

    # 2) 分析數據 + snapshot 摘要（loop_report + 各夾封數 + 不變量檢查）
    rep = server.loop_report()
    snap = server.snapshot()
    snap_summary = {name: len(msgs) for name, msgs in snap.items()}
    analytics = {
        "loop_report": rep,
        "snapshot_counts": snap_summary,
        "connections": server._connections,
        **(extra or {}),
    }
    an_path = os.path.join(OUT_DIR, f"{tag}.analytics.json")
    with open(an_path, "w", encoding="utf-8") as f:
        json.dump(analytics, f, ensure_ascii=False, indent=2, default=str)
    written.append(an_path)

    # 3) wire transcript 取樣（前 60 + 後 30 條；token 為假值，無真實機密）
    wire_path = os.path.join(OUT_DIR, f"{tag}.wire-sample.log")
    lines = [f"C: {b!r}" for b in server.wire_in]
    with open(wire_path, "w", encoding="utf-8") as f:
        f.write(f"# {tag} —— C->S 命令列 wire 取樣（總 {len(lines)} 條；token 為假值）\n\n")
        if len(lines) <= 90:
            f.write("\n".join(lines))
        else:
            f.write("\n".join(lines[:60]))
            f.write(f"\n\n... （中間 {len(lines) - 90} 條省略）...\n\n")
            f.write("\n".join(lines[-30:]))
    written.append(wire_path)
    return written


# ── 跨版本守恆：決定性「指紋」黃金基準 ──────────────────────────────────────────
# loop_report 的協定工作量數據與 snapshot 封數皆**位元組級決定性**（與機器/負載無關——imaplib
# 隨機 tag 前綴長度恆為 4 字、不改變任何計數/bytes），故可凍結為基準、逐版精確比對：任何
# round-trips / 命令次數 / 冗餘 / bytes / 各夾封數漂移都會被抓出（效率退步或結果變動）。牆鐘秒數
# 非決定性、不入指紋（僅 advisory 印出，不當閘門）。有意改動以 `--update` 重新祝福（diff 進 PR 受 SR 檢視）。

FINGERPRINT_FIELDS = (
    "roundtrips", "command_counts", "fetches_per_folder",
    "redundant_full_folder_reads", "redundant_selects",
    "authentications", "reconnects", "destructive_ops",
    "bytes_in", "bytes_out", "connections",
)


def fingerprint(server: ImapServer) -> dict:
    """場景的決定性指紋：協定工作量 + 最終各夾封數 + 注入故障摘要。"""
    rep = server.loop_report()
    fp = {k: rep[k] for k in FINGERPRINT_FIELDS}
    fp["snapshot_counts"] = {name: len(msgs) for name, msgs in server.snapshot().items()}
    fp["faults"] = sorted(f"{fe['kind']}:{fe['op']}:{fe['detail']}" for fe in rep["fault_events"])
    return fp


def _baseline_path(tag: str) -> str:
    return os.path.join(BASELINE_DIR, f"{tag}.json")


def diff_baseline(tag: str, fp: dict) -> "tuple[str, list]":
    """回 (status, diffs)。status：UPDATED（--update 已寫入）/ NEW（無基準）/ MATCH / DRIFT。"""
    path = _baseline_path(tag)
    if UPDATE_BASELINES:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fp, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        return "UPDATED", []
    if not os.path.exists(path):
        return "NEW", []  # 無基準（新場景）；以 `--update` 種下
    with open(path, encoding="utf-8") as f:
        base = json.load(f)
    diffs = [(k, base.get(k), fp.get(k)) for k in sorted(set(fp) | set(base)) if fp.get(k) != base.get(k)]
    return ("DRIFT" if diffs else "MATCH"), diffs


# ── 場景結果記錄 ─────────────────────────────────────────────────────────────

class Result:
    def __init__(self, tag: str, title: str) -> None:
        self.tag = tag
        self.title = title
        self.checks: list[tuple[str, bool, str]] = []
        self.error: str | None = None
        self.elapsed = 0.0
        self.logs: list[str] = []
        self.baseline_status = "N/A"
        self.baseline_diffs: list = []
        self.fingerprint: dict | None = None

    def check(self, desc: str, ok: bool, detail: str = "") -> None:
        self.checks.append((desc, bool(ok), detail))

    @property
    def passed(self) -> bool:
        return (self.error is None
                and all(ok for _, ok, _ in self.checks)
                and self.baseline_status != "DRIFT")


RESULTS: list[Result] = []


def scenario(tag: str, title: str):
    def deco(fn):
        def wrapper():
            r = Result(tag, title)
            mp = MonkeyPatch()
            mp.setattr("mailkeeper.imap_client.time.sleep", lambda s: None)  # 不真睡退避
            t0 = time.time()
            server = None
            try:
                server = fn(mp, r)
            except Exception:
                r.error = traceback.format_exc()
            finally:
                r.elapsed = time.time() - t0
                mp.undo()
            if server is not None:
                r.logs = write_logs(tag, server, extra={"scenario": title, "elapsed_s": round(r.elapsed, 2)})
                r.fingerprint = fingerprint(server)
                r.baseline_status, r.baseline_diffs = diff_baseline(tag, r.fingerprint)
            RESULTS.append(r)
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] {tag} {title}  ({r.elapsed:.1f}s, 牆鐘 advisory)  baseline={r.baseline_status}")
            for desc, ok, detail in r.checks:
                if not ok:
                    print(f"        ✗ {desc} :: {detail}")
            for field, old, new in r.baseline_diffs:
                print(f"        Δ baseline 漂移 {field}: {old!r} -> {new!r}")
            if r.error:
                print("        ✗ EXCEPTION:\n" + r.error)
            return r
        wrapper._scenario = (tag, title)
        return wrapper
    return deco


def _rows(uids, src, tgt):
    return [ClassificationRow(str(u), src, tgt) for u in uids]


# ── S1：30k 全量讀取（happy）─────────────────────────────────────────────────

@scenario("S1", "30k 全量匯出讀取（happy path）")
def s1(mp, r):
    server = fresh_30k_server()
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    progress = []
    headers = client.list_headers("INBOX", on_progress=lambda d, t: progress.append((d, t)))
    by_uid = {h.uid: h for h in headers}

    r.check("讀回 30,000 封", len(headers) == N, f"got {len(headers)}")
    r.check("UID 全非空", all(h.uid for h in headers), "")
    r.check("無重複/無遺漏", len({h.uid for h in headers}) == N, f"unique {len({h.uid for h in headers})}")
    r.check("CJK 主旨逐字等價", by_uid["1000"].subject == "週報 Q1 報告", by_uid["1000"].subject)
    r.check("CJK 寄件者 encoded-word 等價", by_uid["1000"].sender == "王經理 <boss@x.com>", by_uid["1000"].sender)
    r.check("emoji 主旨等價", by_uid["1001"].subject == "🎉 Happy New Year 新年快樂", by_uid["1001"].subject)
    r.check("FW 主旨等價", by_uid["1002"].subject == "FW: 推薦職務", by_uid["1002"].subject)
    r.check("空主旨等價", by_uid["1003"].subject == "", repr(by_uid["1003"].subject))
    r.check("超長主旨等價", by_uid["1004"].subject == "L" * 200, str(len(by_uid["1004"].subject)))
    rep = server.loop_report()
    r.check("UID FETCH 批次=⌈30000/50⌉=600", server.command_count("UID FETCH") == 600,
            str(server.command_count("UID FETCH")))
    r.check("fetches_per_folder = {INBOX:600}（多批非冗餘）", rep["fetches_per_folder"] == {"INBOX": 600},
            str(rep["fetches_per_folder"]))
    r.check("UID SEARCH 僅 1 次", rep["command_counts"].get("UID SEARCH") == 1,
            str(rep["command_counts"].get("UID SEARCH")))
    fetched = []
    for op in server.commands("UID FETCH"):
        fetched.extend(op.affected_uids)
    r.check("每 UID 恰抓一次（30000 distinct、零重複重抓）", len(fetched) == len(set(fetched)) == N,
            f"fetched={len(fetched)} unique={len(set(fetched))}")
    r.check("整夾標頭零冗餘重抓（同 UID 不重抓）", rep["redundant_full_folder_reads"] == {},
            str(rep["redundant_full_folder_reads"]))
    r.check("零破壞性操作（唯讀）", rep["destructive_ops"] == 0, str(rep["destructive_ops"]))
    r.check("零重連（happy）", rep["reconnects"] == 0, str(rep["reconnects"]))
    r.check("進度首批=(50,30000) 末批=(30000,30000)",
            progress[0] == (50, N) and progress[-1] == (N, N), f"{progress[0]}..{progress[-1]}")
    r.check("進度嚴格遞增不重複", all(progress[i][0] < progress[i + 1][0] for i in range(len(progress) - 1)), "")
    try:
        server.assert_all_fetches_request_uid()
        r.check("每個 FETCH 都索取 UID", True)
    except AssertionError as e:
        r.check("每個 FETCH 都索取 UID", False, str(e))
    return server


# ── S2：30k 全量讀取 + 中途 token 過期透明重連（可續傳，不整批重抓）──────────────

@scenario("S2", "30k 全量讀取 + 中途 EOF 透明重連（可續傳）")
def s2(mp, r):
    server = fresh_30k_server()
    server.arm_expiry(before_op="fetch", nth=300, mode="eof")  # 第 300 批前斷線
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    progress = []
    headers = client.list_headers("INBOX", on_progress=lambda d, t: progress.append((d, t)))

    r.check("讀回 30,000 封", len(headers) == N, f"got {len(headers)}")
    r.check("UID 全非空", all(h.uid for h in headers), "")
    r.check("無重複/無遺漏", len({h.uid for h in headers}) == N, "")
    fetched = []
    for op in server.commands("UID FETCH"):
        fetched.extend(op.affected_uids)
    r.check("每 UID 至多抓一次（續傳、非整批重抓）", len(fetched) == len(set(fetched)) == N,
            f"fetched={len(fetched)} unique={len(set(fetched))}")
    r.check("UID FETCH 成功批次=600（失敗批未計）", server.command_count("UID FETCH") == 600,
            str(server.command_count("UID FETCH")))
    rep = server.loop_report()
    r.check("發生重連（authentications>=2）", rep["authentications"] >= 2, str(rep["authentications"]))
    r.check("重連後重選不算冗餘（redundant_selects=0）", rep["redundant_selects"] == 0, str(rep["redundant_selects"]))
    r.check("注入故障含 eof", any(fe["detail"] == "eof" for fe in rep["fault_events"]), str(rep["fault_events"][:2]))
    r.check("進度跨重連嚴格遞增不回退",
            all(progress[i][0] < progress[i + 1][0] for i in range(len(progress) - 1)),
            f"{progress[:3]}...{progress[-2:]}")
    r.check("進度末批=(30000,30000)", progress[-1] == (N, N), str(progress[-1]))
    return server


# ── S3：重連後 UIDVALIDITY 變更（信箱重建、UID 重配）→ 安全整批重抓 ───────────────

@scenario("S3", "30k 讀取 + 重連後 UIDVALIDITY 變更（信箱重建）→ 安全重抓")
def s3(mp, r):
    server = fresh_30k_server()
    server.arm_expiry(before_op="fetch", nth=300, mode="eof")
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    orig = client._reconnect

    def reconnect_then_rebuild():
        orig()
        server.set_uidvalidity("INBOX", 999999, reassign_uids=True)  # 斷線期間信箱被重建、UID 全重配

    mp.setattr(client, "_reconnect", reconnect_then_rebuild)
    headers = client.list_headers("INBOX")

    r.check("安全重抓後完整 30,000 封", len(headers) == N, f"got {len(headers)}")
    r.check("UID 全非空", all(h.uid for h in headers), "")
    r.check("無重複", len({h.uid for h in headers}) == N, "")
    r.check("UID 已全部重配（皆 >= 9000）", all(int(h.uid) >= 9000 for h in headers),
            f"min={min(int(h.uid) for h in headers)}")
    r.check("重建後仍正確解碼 CJK 主旨", any(h.subject == "週報 Q1 報告" for h in headers), "")
    r.check("重連後重新 SEARCH（未沿用過時進度）", server.command_count("UID SEARCH") >= 2,
            str(server.command_count("UID SEARCH")))
    r.check("發生重連", server.command_count("AUTHENTICATE") >= 2, str(server.command_count("AUTHENTICATE")))
    return server


# ── S4：分類 + 搬移 3,000（INBOX→Archive，happy 全流程）──────────────────────────

@scenario("S4", "分類 + 搬移 3,000（INBOX→Archive，happy 全流程）")
def s4(mp, r):
    server = fresh_30k_server()
    before = server.snapshot()
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    cache = classifier.ClassifyCache()
    items = classifier.build_report(client, _rows(MOVE_UIDS, "INBOX", "Archive"), cache=cache)
    rep_after_report = server.loop_report()
    results = classifier.execute(client, items, cache=cache)

    r.check("3,000 列全部搬移成功", len(results) == MOVE_N and all(x.ok for x in results),
            f"ok={sum(x.ok for x in results)}/{len(results)}")
    r.check("Archive 恰增 3,000（無重複複本）", len(server.mailboxes["Archive"]) == MOVE_N,
            str(len(server.mailboxes["Archive"])))
    r.check("INBOX 恰減為 27,000", len(server.mailboxes["INBOX"]) == N - MOVE_N,
            str(len(server.mailboxes["INBOX"])))
    after = server.snapshot()
    inbox_uids_after = {u for u, _ in after["INBOX"]}
    r.check("3,000 搬移封全部離開 INBOX", set(MOVE_UIDS).isdisjoint(inbox_uids_after), "")
    deleted_after = {u for u, fl in after["INBOX"] if DELETED in fl}
    r.check("使用者 50 封 \\Deleted 全程不被波及", USER_DELETED_UIDS <= deleted_after,
            f"present={len(USER_DELETED_UIDS & deleted_after)}/50")
    r.check("報告階段以 UID SEARCH 判存在、零整夾標頭 FETCH",
            server.command_count("UID FETCH") == 0 and rep_after_report["command_counts"].get("UID SEARCH", 0) >= 1,
            f"FETCH={server.command_count('UID FETCH')}")
    r.check("批次 UID MOVE = ⌈3000/200⌉ = 15", server.command_count("UID MOVE") == 15,
            str(server.command_count("UID MOVE")))
    r.check("同來源夾零冗餘 SELECT", server.redundant_selects() == 0, str(server.redundant_selects()))
    r.check("零整夾標頭冗餘重抓", server.loop_report()["redundant_full_folder_reads"] == {}, "")
    return server


# ── S5：分類 + 搬移 3,000 + 中途 token 過期透明重連（冪等續傳，無重複複本）──────────

@scenario("S5", "分類 + 搬移 3,000 + 搬移中途 EOF 透明重連（冪等）")
def s5(mp, r):
    server = fresh_30k_server()
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    cache = classifier.ClassifyCache()
    items = classifier.build_report(client, _rows(MOVE_UIDS, "INBOX", "Archive"), cache=cache)
    server.arm_expiry(before_op="move", nth=2, mode="eof")  # 第 2 批 UID MOVE 前斷線
    results = classifier.execute(client, items, cache=cache)

    r.check("3,000 列全部搬移成功", len(results) == MOVE_N and all(x.ok for x in results),
            f"ok={sum(x.ok for x in results)}/{len(results)}")
    r.check("Archive 恰=3,000（重連重試不產生重複複本）", len(server.mailboxes["Archive"]) == MOVE_N,
            str(len(server.mailboxes["Archive"])))
    r.check("INBOX 恰減為 27,000", len(server.mailboxes["INBOX"]) == N - MOVE_N,
            str(len(server.mailboxes["INBOX"])))
    after = server.snapshot()
    deleted_after = {u for u, fl in after["INBOX"] if DELETED in fl}
    r.check("使用者 50 封 \\Deleted 全程不被波及", USER_DELETED_UIDS <= deleted_after,
            f"present={len(USER_DELETED_UIDS & deleted_after)}/50")
    rep = server.loop_report()
    r.check("發生重連（authentications>=2）", rep["authentications"] >= 2, str(rep["authentications"]))
    r.check("重連後重選不算冗餘（redundant_selects=0）", rep["redundant_selects"] == 0, str(rep["redundant_selects"]))
    r.check("注入故障含 eof（move 期間）", any(fe["detail"] == "eof" for fe in rep["fault_events"]), "")
    return server


# ── S6：多目標夾分類（Archive / 台北 CJK / 封存/2026 巢狀新建 / Junk Email 含空白）────

@scenario("S6", "多目標夾搬移 3,000（mUTF-7 + 引號 + 自動新建夾）")
def s6(mp, r):
    server = fresh_30k_server()
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    cache = classifier.ClassifyCache()
    rows = (
        _rows(range(1000, 2000), "INBOX", "Archive")        # 1000 → Archive（既有空夾）
        + _rows(range(2000, 2500), "INBOX", "台北")          # 500 → 台北（CJK，既有）
        + _rows(range(2500, 3000), "INBOX", "封存/2026")     # 500 → 封存/2026（巢狀，需新建）
        + _rows(range(3000, 4000), "INBOX", "Junk Email")    # 1000 → Junk Email（含空白，既有）
    )
    items = classifier.build_report(client, rows, cache=cache)
    results = classifier.execute(client, items, cache=cache)

    r.check("3,000 列全部搬移成功", len(results) == MOVE_N and all(x.ok for x in results),
            f"ok={sum(x.ok for x in results)}/{len(results)}")
    r.check("Archive 收 1,000", len(server.mailboxes["Archive"]) == 1000, str(len(server.mailboxes["Archive"])))
    r.check("台北（CJK）收 500（原1+500=501）", len(server.mailboxes["台北"]) == 501,
            str(len(server.mailboxes["台北"])))
    r.check("封存/2026（巢狀）自動新建並收 500",
            "封存/2026" in server.mailboxes and len(server.mailboxes["封存/2026"]) == 500,
            str(server.mailboxes.get("封存/2026", "缺")))
    r.check("Junk Email（含空白）收 1,000", len(server.mailboxes["Junk Email"]) == 1000,
            str(len(server.mailboxes["Junk Email"])))
    r.check("INBOX 恰減為 27,000", len(server.mailboxes["INBOX"]) == N - MOVE_N,
            str(len(server.mailboxes["INBOX"])))
    r.check("新建夾發出 CREATE", server.command_count("CREATE") == 1, str(server.command_count("CREATE")))
    after = server.snapshot()
    deleted_after = {u for u, fl in after["INBOX"] if DELETED in fl}
    r.check("使用者 50 封 \\Deleted 全程不被波及", USER_DELETED_UIDS <= deleted_after, "")
    return server


# ── S7：異常伺服器 drop_uid —— 即使索取 UID 也不回 → 產品大聲失敗，不產生 uid 全空汙染 ──

@scenario("S7", "異常伺服器 drop_uid → 產品大聲失敗（不產生缺 UID 汙染）")
def s7(mp, r):
    from mailkeeper.imap_client import BackendError
    server = ImapServer({"INBOX": build_inbox(120), "Archive": []}, drop_uid=True)
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    raised = None
    try:
        client.list_headers("INBOX")
    except Exception as e:  # noqa: BLE001
        raised = e
    r.check("list_headers 大聲拋錯（非靜默回不完整）", isinstance(raised, BackendError),
            type(raised).__name__ if raised else "未拋錯")
    r.check("錯誤訊息點出無法解析 UID",
            raised is not None and "UID" in str(raised), str(raised)[:80] if raised else "")
    return server


# ── S8：異常伺服器 fail_fetch —— 批次 FETCH 一律 NO → 產品大聲失敗，不靜默回不完整標頭 ──

@scenario("S8", "異常伺服器 fail_fetch → 產品大聲失敗（不靜默回不完整標頭）")
def s8(mp, r):
    from mailkeeper.imap_client import BackendError
    server = ImapServer({"INBOX": build_inbox(120), "Archive": []}, fail_fetch=True)
    client = connected_client(mp, server, token_provider=lambda: FAKE_TOKEN)
    raised = None
    try:
        client.list_headers("INBOX")
    except Exception as e:  # noqa: BLE001
        raised = e
    r.check("list_headers 大聲拋錯", isinstance(raised, BackendError),
            type(raised).__name__ if raised else "未拋錯")
    r.check("錯誤訊息點出讀取標頭失敗",
            raised is not None and "標頭" in str(raised), str(raised)[:80] if raised else "")
    return server


def main(argv: "list[str] | None" = None) -> int:
    global UPDATE_BASELINES
    args = sys.argv[1:] if argv is None else argv
    UPDATE_BASELINES = "--update" in args
    mode = "更新基準（--update，重新祝福）" if UPDATE_BASELINES else "比對基準（任何漂移即 FAIL）"
    print(f"== MailKeeper 全端 E2E（{N:,} 封 / 搬移 {MOVE_N:,} 封）｜{mode} ==")
    print(f"   trace log：{OUT_DIR}")
    print(f"   黃金基準：{BASELINE_DIR}\n")
    for fn in (s1, s2, s3, s4, s5, s6, s7, s8):
        fn()

    # 彙整報告
    os.makedirs(OUT_DIR, exist_ok=True)
    report_path = os.path.join(OUT_DIR, "E2E-REPORT.md")
    n_pass = sum(1 for r in RESULTS if r.passed)
    n_drift = sum(1 for r in RESULTS if r.baseline_status == "DRIFT")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# MailKeeper 全端 E2E 報告\n\n")
        f.write(f"- 規模：INBOX **{N:,}** 封、搬移 **{MOVE_N:,}** 封\n")
        f.write(f"- 引擎：tests/imap_server.py::ImapServer + SimIMAP4_SSL（真 imaplib over 線級引擎）\n")
        f.write(f"- 模式：{mode}\n")
        f.write(f"- 結果：**{n_pass}/{len(RESULTS)}** 場景通過"
                + (f"；**{n_drift} 個基準漂移**" if n_drift else "；基準全部相符") + "\n")
        f.write("- 牆鐘耗時為 advisory（隨機器/負載浮動，不納入基準、不當閘門）；"
                "效率/結果守恆由決定性指紋把關。\n\n")
        f.write("| 場景 | 標題 | 檢查 | 基準 | 結果 | 耗時 |\n|---|---|---|---|---|---|\n")
        for r in RESULTS:
            n_ok = sum(1 for _, ok, _ in r.checks if ok)
            f.write(f"| {r.tag} | {r.title} | {n_ok}/{len(r.checks)} | {r.baseline_status} | "
                    f"{'✅ PASS' if r.passed else '❌ FAIL'} | {r.elapsed:.1f}s |\n")
        f.write("\n## 各場景檢查明細\n")
        for r in RESULTS:
            f.write(f"\n### {r.tag} — {r.title}（{'PASS' if r.passed else 'FAIL'}，基準 {r.baseline_status}）\n")
            for desc, ok, detail in r.checks:
                f.write(f"- {'✅' if ok else '❌'} {desc}" + (f" — `{detail}`" if (detail and not ok) else "") + "\n")
            for field, old, new in r.baseline_diffs:
                f.write(f"- ⚠️ 基準漂移 `{field}`：`{old}` → `{new}`\n")
            if r.error:
                f.write(f"\n```\n{r.error}\n```\n")
            f.write(f"\nLog：{', '.join(os.path.basename(p) for p in r.logs)}\n")
    print(f"\n== 彙整：{n_pass}/{len(RESULTS)} 場景通過"
          + (f"，{n_drift} 個基準漂移" if n_drift else "，基準全部相符") + " ==")
    print(f"   報告：{report_path}")
    return 0 if n_pass == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
