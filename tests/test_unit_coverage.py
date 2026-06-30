"""群 1 覆蓋補強 —— seam 以外的防禦/錯誤分支（純單元，無網路、無 imaplib）。

對應「總覆蓋率盲區」評估後選定補強的有意義分支：organizer 真實執行、classifier 不可行/
ReauthRequired/TOCTOU、config 驗證、CSV 寫檔錯誤、console/progress 永不崩潰、menu EOF、
buildinfo 回退。純 I/O 包裝與 auth/MSAL/入口另以 `# pragma: no cover` 誠實標記，不在此。
"""
from __future__ import annotations

import json
import sys

import pytest

from mailkeeper import buildinfo, config, config_store, console, csv_io, menu, progress
from mailkeeper.classifier import (
    CANDIDATE,
    INFEASIBLE,
    ReportItem,
    build_report,
    execute,
)
from mailkeeper.csv_io import ClassificationRow
from mailkeeper.domain import MailHeader, ReauthRequired
from mailkeeper.organizer import MailOrganizer, Rule, subject_contains


def _raise(*a, **k):
    raise OSError("boom")


# ── organizer：dry_run=False 的真實執行分支（57-63）──────────────────────────

def test_organizer_real_run_executes_all_actions(make_backend):
    """run(dry_run=False)：命中規則時實際 ensure_folder→move、mark_read、flag（非試跑）。"""
    backend = make_backend(headers=[MailHeader("1", "newsletter weekly", "a@x.com", "Mon")])
    rules = [Rule("n", subject_contains("newsletter"), dest_folder="News", mark_read=True, flag=True)]
    MailOrganizer(backend, rules).run(dry_run=False)
    assert ("folder", "News") in backend.actions
    assert ("move", "1", "News", "INBOX") in backend.actions
    assert ("read", "1") in backend.actions
    assert ("flag", "1") in backend.actions


# ── classifier：不可行 / ReauthRequired 透傳 / 執行時來源已不存在 ──────────────

class _ReauthBackend:
    """最小後端：分類執行時 move_many 擲 ReauthRequired（驗 execute 透傳、不當單列失敗）。"""
    def list_folders(self): return ["INBOX", "Archive"]
    def list_uids(self, folder="INBOX", *, on_progress=None): return {"1"}
    def ensure_folder(self, folder): pass
    def move_many(self, uids, dest, mailbox="INBOX"): raise ReauthRequired("relogin")


def test_build_report_marks_missing_uid_infeasible():
    """缺 uid（或 current_folder）→ INFEASIBLE「缺 uid 或 current_folder」（classifier 103）。"""
    backend = _ReauthBackend()
    rows = [ClassificationRow("", "INBOX", "Archive")]
    items = build_report(backend, rows)
    assert items[0].status == INFEASIBLE and "缺 uid" in items[0].reason


def test_execute_reraises_reauth_required():
    """execute 中 move_many 擲 ReauthRequired → 立即外拋（classifier 199），由 cli 乾淨停止。"""
    backend = _ReauthBackend()
    items = [ReportItem(ClassificationRow("1", "INBOX", "Archive"), CANDIDATE)]
    with pytest.raises(ReauthRequired):
        execute(backend, items)


def test_execute_marks_candidate_gone_at_execution():
    """候選的來源 UID 於執行當下已不存在（TOCTOU）→ 該列記為失敗，不連坐（classifier 205）。"""
    class _EmptyBackend:
        def list_folders(self): return ["INBOX", "Archive"]
        def list_uids(self, folder="INBOX", *, on_progress=None): return set()  # 來源已空
        def ensure_folder(self, folder): pass
        def move_many(self, uids, dest, mailbox="INBOX"): return {}

    items = [ReportItem(ClassificationRow("999", "INBOX", "Archive"), CANDIDATE)]
    results = execute(_EmptyBackend(), items)
    assert results[0].ok is False and "執行時已不存在" in results[0].error


# ── config_store：非整數 port / 非物件 JSON（101-102, 133）────────────────────

def _write_cfg(tmp, obj) -> None:
    (tmp / "config.json").write_text(json.dumps(obj), encoding="utf-8")


def test_load_non_integer_port_raises(tmp_cwd):
    """imap_port 非整數 → 友善 ConfigError（_as_int，101-102）。"""
    _write_cfg(tmp_cwd, {"client_id": "abc", "email": "me@x.com", "imap_port": "not-int"})
    with pytest.raises(config_store.ConfigError):
        config_store.load()


def test_load_non_object_json_raises(tmp_cwd):
    """config.json 是合法 JSON 但非物件（最外層為陣列）→ ConfigError（133）。"""
    (tmp_cwd / "config.json").write_text("[]", encoding="utf-8")
    with pytest.raises(config_store.ConfigError):
        config_store.load()


# ── csv_io：寫檔失敗 → CsvError（80-81, 92-93）────────────────────────────────

def test_write_worksheet_oserror_becomes_csverror(tmp_path):
    bad = tmp_path / "nope" / "w.csv"  # 父目錄不存在 → open 擲 OSError
    with pytest.raises(csv_io.CsvError):
        csv_io.write_worksheet([MailHeader("1", "S", "a", "Mon", "b")], "INBOX", bad)


def test_write_folders_oserror_becomes_csverror(tmp_path):
    bad = tmp_path / "nope" / "f.csv"
    with pytest.raises(csv_io.CsvError):
        csv_io.write_folders(["INBOX"], bad)


# ── console：永不因編碼/flush 崩潰（30-33, 36, 44, 49-50, 70）──────────────────

def test_safewriter_flush_swallows_error():
    class _RaiseFlush:
        encoding = "utf-8"
        def write(self, s): return len(s)
        def flush(self): raise OSError("x")

    console._SafeWriter(_RaiseFlush()).flush()  # 30-33：吞例外、不外拋


def test_safewriter_delegates_unknown_attr():
    class _Wrapped:
        encoding = "utf-8"
        custom = "hi"
        def write(self, s): return len(s)
        def flush(self): pass

    assert console._SafeWriter(_Wrapped()).custom == "hi"  # 36：__getattr__ 委派底層


def test_setup_is_idempotent_when_already_wrapped(monkeypatch):
    import io
    monkeypatch.setattr(sys, "stdout", console._SafeWriter(io.StringIO()))  # 已包裹
    console.setup()  # 44：偵測已是 _SafeWriter → continue、不重複包
    assert isinstance(sys.stdout, console._SafeWriter)


def test_setup_tolerates_reconfigure_failure(monkeypatch):
    class _BadReconfig:
        encoding = "utf-8"
        def write(self, s): return len(s)
        def flush(self): pass
        def reconfigure(self, **k): raise ValueError("no")

    monkeypatch.setattr(sys, "stdout", _BadReconfig())
    console.setup()  # 49-50：reconfigure 失敗被容忍，仍包成 _SafeWriter
    assert isinstance(sys.stdout, console._SafeWriter)


def test_safe_print_flushes_when_requested():
    import io
    flushed = {"n": 0}

    class _S(io.StringIO):
        def flush(self): flushed["n"] += 1

    s = _S()
    console.safe_print("x", file=s, flush=True)  # 70：flush=True → target.flush()
    assert flushed["n"] >= 1 and "x" in s.getvalue()


# ── progress：close 永不崩潰主流程（65-66）────────────────────────────────────

def test_progress_close_swallows_stream_error():
    class _BadOnNewline:
        def isatty(self): return True
        def write(self, s):
            if s == "\n":
                raise OSError("boom")  # close 寫換行時失敗
            return len(s)
        def flush(self): pass

    p = progress._Progress("x", _BadOnNewline(), 0)  # threshold 0 → 互動 TTY 即啟用
    p.update(1, 2)   # 寫一次（_wrote=True）
    p.close()        # 65-66：寫換行失敗被吞、不外拋


# ── menu：EOF（管道/stdin 關閉）→ 乾淨離開（29-30）────────────────────────────

def test_menu_returns_on_eof():
    def _eof(_prompt):
        raise EOFError

    menu.run([("x", lambda: None)], read=_eof, out=lambda *a, **k: None)  # 29-30：EOF → return（不拋）


# ── buildinfo：無 build 烙印時的回退（22-27）──────────────────────────────────

def test_build_stamp_falls_back_to_mtime(monkeypatch):
    monkeypatch.setitem(sys.modules, "mailkeeper._buildinfo", None)  # import 擲錯 → 走回退
    out = buildinfo.build_stamp()
    assert len(out) == 15 and out[8] == "-"  # YYYYMMDD-HHMMSS（檔案 mtime）


def test_build_stamp_unknown_when_mtime_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "mailkeeper._buildinfo", None)
    monkeypatch.setattr("os.path.getmtime", _raise)  # mtime 也失敗 → "unknown"
    assert buildinfo.build_stamp() == "unknown"
