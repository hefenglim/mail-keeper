"""IMAP 存取模組 —— 所有 IMAP 連線與協定操作都封裝在這裡。

設計原則：
  1. 對外只暴露「領域語意」的方法 (列出標題、搬移、標記已讀…)，
     不讓 imaplib 的協定細節外洩到上層。
  2. 回傳值是乾淨的 MailHeader 資料類別，而非 raw IMAP response。
  3. 未來若要改用 Microsoft Graph API，只要做一個提供相同方法的類別，
     organizer 等上層完全不需更動 (見 organizer.MailBackend 介面)。
"""
from __future__ import annotations

import base64
import email
import imaplib
import re
import socket
import ssl
import time
from dataclasses import dataclass
from email.header import decode_header
from typing import Any, Callable, Iterator, TypeVar

import charset_normalizer

from . import config

# 後端中立的錯誤別名：讓上層 (cli) 不必直接 import imaplib，維持 seam 純度。
BackendError = imaplib.IMAP4.error


class ReauthRequired(Exception):
    """需使用者重新登入的終結訊號（非暫時性、不重試）。

    後端中立：定義在此而非 auth.py，使 `imap_client` 不必 import MSAL（憲法 Principle I）；
    `auth` 與 `cli` 反向 import 本類別。訊息**絕不**含 token/secret（Principle IV）。
    """


_FETCH_BATCH = 50  # 每批 UID FETCH 的封數：減少往返、並讓進度於下載期間分批前進
_UID_RE = re.compile(rb"UID (\d+)")

# 視為「session 失效/連線中斷、需重連」的錯誤訊息標記（research R4；對照真實 Outlook log）。
_SESSION_LOST_MARKERS = ("AccessTokenExpired", "Session invalidated", "AUTHENTICATIONFAILED")

_T = TypeVar("_T")


def _chunked(seq: list[Any], size: int) -> Iterator[list[Any]]:
    """等分切批：yield 連續的子序列（純函式，離線可測）。"""
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _extract_uid(meta: Any) -> str:
    """從 IMAP FETCH 回應的中介列（如 b'1 (UID 10 BODY[...] {123}'）取出 UID。"""
    if isinstance(meta, (bytes, bytearray)):
        m = _UID_RE.search(meta)
        if m:
            return m.group(1).decode()
    return ""


@dataclass(frozen=True)
class MailHeader:
    """一封郵件的標題資訊 (上層只看得到這個，看不到 imaplib)。"""

    uid: str
    subject: str
    sender: str
    date: str
    recipients: str = ""


def _unfold(value: str) -> str:
    """攤平折疊標題：把換行＋後續空白還原為單一空白，讓被拆段的 encoded-word 重新相鄰。"""
    return re.sub(r"\r?\n[ \t]+", " ", value)


def _decode_chunk(raw: Any, charset: str | None) -> str:
    """解碼單一 decode_header 片段；宣告字集失敗則用偵測回復，永不拋例外。"""
    if isinstance(raw, str):
        return raw
    if charset:
        try:
            return raw.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    best = charset_normalizer.from_bytes(raw).best()
    if best is not None:
        return str(best)
    return raw.decode("utf-8", "replace")


def _decode(value: str | None) -> str:
    """處理 MIME encoded-word (=?UTF-8?...?=)，含折疊多段與未宣告字集；永不拋例外，永遠回傳 str。"""
    if not value:
        return ""
    try:
        return "".join(
            _decode_chunk(raw, charset)
            for raw, charset in decode_header(_unfold(value))
        )
    except Exception:
        return value if isinstance(value, str) else ""


def _decode_mutf7(name: str) -> str:
    """解 IMAP modified-UTF-7 資料夾名稱（RFC 3501 §5.1.3）。"""
    if "&" not in name:
        return name
    out: list[str] = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        end = name.find("-", i + 1)
        if end == -1:
            out.append(name[i:])
            break
        chunk = name[i + 1 : end]
        if chunk == "":
            out.append("&")
        else:
            b64 = chunk.replace(",", "/")
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            try:
                out.append(base64.b64decode(b64).decode("utf-16-be"))
            except Exception:
                out.append(name[i : end + 1])
        i = end + 1
    return "".join(out)


def _parse_folder_name(line: Any) -> str:
    """從一條 IMAP LIST 回應取出資料夾名稱（處理引號與 modified-UTF-7）。"""
    s = line.decode() if isinstance(line, (bytes, bytearray)) else str(line)
    m = re.search(r'"((?:[^"\\]|\\.)*)"\s*$', s)
    if m:
        raw = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
    else:
        parts = s.split()
        raw = parts[-1] if parts else ""
    return _decode_mutf7(raw)


class OutlookIMAPClient:
    """封裝 Outlook.com 的 IMAP 操作。建議搭配 with 語法使用。"""

    def __init__(
        self,
        email_account: str,
        access_token: str,
        *,
        host: str | None = None,
        port: int | None = None,
        timeout: float | None = None,
        token_provider: Callable[[], str] | None = None,
        on_status: Callable[[str], None] | None = None,
        max_consecutive_failures: int = config.MAX_CONSECUTIVE_FAILURES,
        max_reconnect_attempts: int = config.MAX_RECONNECT_ATTEMPTS,
        max_retries_per_op: int = config.MAX_RETRIES_PER_OP,
        backoff_base_seconds: float = config.BACKOFF_BASE_SECONDS,
        backoff_cap_seconds: float = config.BACKOFF_CAP_SECONDS,
    ) -> None:
        self._email = email_account
        self._token = access_token
        self._host = host if host is not None else config.IMAP_HOST
        self._port = port if port is not None else config.IMAP_PORT
        self._timeout = timeout if timeout is not None else config.IMAP_TIMEOUT
        self._imap: imaplib.IMAP4_SSL | None = None
        self._selected: tuple[str, bool] | None = None  # 目前選取的 (mailbox, readonly)；免重複 SELECT（P3）
        # R7 韌性：注入式 token 續期 / 狀態回呼 / 重連與重試上限（後端中立，維持 seam 純度）。
        self._token_provider = token_provider
        self._on_status = on_status
        # classifier 讀此屬性決定連續失敗門檻（後端中立、duck-typed；FakeBackend 無此屬性 → 用預設）。
        self.max_consecutive_failures = max_consecutive_failures
        self._max_reconnect_attempts = max(0, max_reconnect_attempts)
        self._max_retries_per_op = max(0, max_retries_per_op)
        self._backoff_base = max(0.0, backoff_base_seconds)
        self._backoff_cap = max(self._backoff_base, backoff_cap_seconds)

    # ---------- 連線管理 ----------
    def connect(self) -> None:
        # 用目前持有的 token 連線（初次＝建構時取得的有效 token；重連前由 _reconnect 先靜默續期更新）。
        self._imap = imaplib.IMAP4_SSL(self._host, self._port, timeout=self._timeout)
        # XOAUTH2 認證字串格式 (注意是 \x01 控制字元，不是空白)
        auth_string = f"user={self._email}\x01auth=Bearer {self._token}\x01\x01"
        self._imap.authenticate("XOAUTH2", lambda _: auth_string.encode())
        self._selected = None  # 新連線：尚未選取任何資料夾（重連後必重新 SELECT）

    # ---------- R7：透明重連 / 有界重試 ----------
    def _status(self, msg: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(msg)  # 後端中立；訊息不含 secret
            except Exception:
                pass  # 狀態提示永不影響主流程

    @staticmethod
    def _is_session_lost(exc: BaseException) -> bool:
        """判斷是否為「session 失效/連線中斷、應重連」（vs 單封資料層失敗）。"""
        if isinstance(exc, (imaplib.IMAP4.abort, OSError, ssl.SSLError, socket.error)):
            return True
        if isinstance(exc, imaplib.IMAP4.error):
            return any(m in str(exc) for m in _SESSION_LOST_MARKERS)
        return False

    def _reconnect(self) -> None:
        """重建連線：登出舊連線（best-effort）→ 靜默續期更新 token → connect() 重新認證。"""
        try:
            if self._imap is not None:
                self._imap.logout()
        except Exception:
            pass
        self._imap = None
        if self._token_provider is not None:
            self._token = self._token_provider()  # 僅靜默續期；無法續期 → ReauthRequired（往外傳、不重試）
        self.connect()

    def _with_reconnect(self, op: Callable[[], _T]) -> _T:
        """執行 op；遇 session 失效/連線中斷 → 靜默續期 + 重連 + 有界退避重試。

        `ReauthRequired` 直接外拋（不重試）；非連線類錯誤照常外拋（維持單列處理/安全 fallback）。
        """
        attempts = 0
        while True:
            try:
                return op()
            except ReauthRequired:
                raise  # 需重新登入 → 終結，由 cli 乾淨停止
            except Exception as exc:
                if not self._is_session_lost(exc) or attempts >= self._max_reconnect_attempts:
                    raise
                attempts += 1
                self._status(f"連線中斷，重新連線中…（第 {attempts}/{self._max_reconnect_attempts} 次）")
                self._sleep_backoff(attempts)
                self._reconnect()
                self._status("已重新連線，繼續處理。")

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(self._backoff_base * (2 ** (attempt - 1)), self._backoff_cap)
        if delay > 0:
            time.sleep(delay)

    def close(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            finally:
                self._imap = None

    def __enter__(self) -> "OutlookIMAPClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def _conn(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            raise RuntimeError("尚未連線，請先呼叫 connect() 或使用 with。")
        return self._imap

    def _ensure_selected(self, mailbox: str, readonly: bool = False) -> None:
        """免重複 SELECT（P3/C2）：僅在未選／資料夾不同／讀寫模式不同時才 SELECT。
        連線／重連後 `self._selected` 已重置為 None，故重連後必重新選取。"""
        if self._selected == (mailbox, readonly):
            return
        self._conn.select(mailbox, readonly=readonly)
        self._selected = (mailbox, readonly)

    # ---------- 讀取 ----------
    def list_folders(self) -> list[str]:
        """列舉信箱所有資料夾名稱（連線中斷會透明重連重試）。"""
        return self._with_reconnect(self._list_folders_impl)

    def _list_folders_impl(self) -> list[str]:
        typ, data = self._conn.list()
        folders: list[str] = []
        if typ == "OK" and data:
            for line in data:
                if not line:
                    continue
                name = _parse_folder_name(line)
                if name:
                    folders.append(name)
        return folders

    def list_headers(
        self, folder: str = "INBOX", *, on_progress: Callable[[int, int], None] | None = None
    ) -> list[MailHeader]:
        """讀取指定資料夾所有郵件的標題 (只抓 header)。以分批 UID FETCH 取得，每批回報
        進度 `on_progress(done, total)`。連線中斷會透明重連並**整批重抓**（唯讀、重跑安全）。"""
        return self._with_reconnect(lambda: self._list_headers_impl(folder, on_progress=on_progress))

    def _list_headers_impl(
        self, folder: str = "INBOX", *, on_progress: Callable[[int, int], None] | None = None
    ) -> list[MailHeader]:
        self._ensure_selected(folder, readonly=True)
        typ, data = self._conn.uid("search", None, "ALL")  # type: ignore[arg-type]  # IMAP SEARCH 允許 charset=None
        if typ != "OK" or not data or data[0] is None:
            return []

        uids = data[0].split()
        total = len(uids)
        headers: list[MailHeader] = []
        done = 0
        for batch in _chunked(uids, _FETCH_BATCH):
            typ, msg_data = self._conn.uid(
                "fetch",
                ",".join(u.decode() for u in batch),
                # 必須顯式索取 UID（置於 BODY 之前）：批次 FETCH 無法像逐封那樣沿用
                # SEARCH 的 UID，只能從回應 metadata 解析；未索取時 Outlook 不回 UID，
                # 會導致每列 uid 全空、產出無法用於分類的工作表（0.5.0 致命回歸）。
                "(UID BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])",
            )
            if typ != "OK":
                # 批次失敗不可靜默吞（會回傳不完整標頭、誤導後續分類）：大聲報錯。
                raise BackendError(f"讀取標頭失敗（{typ}）：{folder} 的批次 FETCH 未成功，請重試。")
            if msg_data:
                for part in msg_data:
                    if not isinstance(part, tuple) or len(part) < 2 or part[1] is None:
                        continue
                    uid = _extract_uid(part[0])
                    if not uid:
                        # 解析不到 UID 即協定異常：寧可大聲中止，也不靜默吐出 uid 空白、
                        # 後續完全無法搬移的「無效工作表」（避免重演靜默資料汙染）。
                        raise BackendError(
                            f"讀取標頭失敗：無法從回應解析 UID（{folder}）。已中止以免"
                            "產生缺 UID 的無效工作表；請重試，若持續發生請回報。"
                        )
                    msg = email.message_from_bytes(part[1])
                    headers.append(
                        MailHeader(
                            uid=uid,
                            subject=_decode(msg.get("Subject")),
                            sender=_decode(msg.get("From")),
                            date=_decode(msg.get("Date")),
                            recipients=_decode(msg.get("To")),
                        )
                    )
            done = min(done + len(batch), total)
            if on_progress is not None:
                on_progress(done, total)
        return headers

    def list_inbox_headers(self, mailbox: str = "INBOX") -> list[MailHeader]:
        """相容保留：等同 list_headers(mailbox)。"""
        return self.list_headers(mailbox)

    def list_uids(
        self, folder: str = "INBOX", *, on_progress: Callable[[int, int], None] | None = None
    ) -> set[str]:
        """讀取指定資料夾現存所有郵件的 UID 集合（只查 UID、**不抓標頭內容**）。

        供分類存在性檢查：一次 `UID SEARCH ALL` 取代整夾標頭 FETCH（大幅減往返/流量）。
        「現存」語意與 `list_headers` 一致——涵蓋信箱中尚未 expunge 的所有郵件（含已標
        `\\Deleted`）。連線中斷會透明重連並重查（唯讀、重跑安全）。逐筆回報進度
        `on_progress(done, total)`（total = 該夾郵件數），使大信箱不像當機。
        """
        return self._with_reconnect(lambda: self._list_uids_impl(folder, on_progress=on_progress))

    def _list_uids_impl(
        self, folder: str = "INBOX", *, on_progress: Callable[[int, int], None] | None = None
    ) -> set[str]:
        self._ensure_selected(folder, readonly=True)
        typ, data = self._conn.uid("search", None, "ALL")  # type: ignore[arg-type]  # IMAP SEARCH 允許 charset=None
        if typ != "OK" or not data or data[0] is None:
            return set()
        uids = [u.decode() for u in data[0].split()]
        total = len(uids)
        # determinate 進度：以該夾郵件數為總數推進至完成（單次往返、不注入人工延遲）
        if on_progress is not None:
            for done in range(1, total + 1):
                on_progress(done, total)
        return set(uids)

    # ---------- 整理動作 ----------
    def ensure_folder(self, folder: str) -> None:
        """確保資料夾存在 (已存在會回 NO，直接忽略即可)。"""
        self._conn.create(folder)

    def move(self, uid: str, dest_folder: str, mailbox: str = "INBOX") -> None:
        """將郵件搬到指定資料夾。優先用 UID MOVE；伺服器不支援時退回 copy→標刪→UID EXPUNGE。
        連線中斷會透明重連並重試本次搬移（搬移自含 select）。主路徑 UID MOVE 重試**冪等**
        （重搬已搬走的郵件為 no-op）；惟 **fallback 非完全冪等**：若於 COPY 成功後、UID EXPUNGE
        前斷線，重試會重做 COPY → 目標夾可能出現**重複複本**（非資料遺失，來源仍正確移除）。
        此為已知限制（見 roadmap-backlog；正解需重試前先偵測既有複本或改用更原子的序列）。

        安全鐵則（破壞性動作，避免資料遺失）：
          1. **COPY 成功才標刪**：copy 未成功就絕不 `\\Deleted`+expunge（沒有複本就刪 = 永久遺失）。
          2. **以 UID EXPUNGE 限定該封**（RFC 4315 UIDPLUS），避免整夾 EXPUNGE 波及其他
             已被標 `\\Deleted` 的郵件；僅在伺服器不支援 UIDPLUS 時才退回整夾 EXPUNGE。
        """
        self._with_reconnect(lambda: self._move_impl(uid, dest_folder, mailbox))

    def _move_impl(self, uid: str, dest_folder: str, mailbox: str) -> None:
        self._ensure_selected(mailbox)
        typ, _ = self._conn.uid("move", uid, dest_folder)
        if typ == "OK":
            return

        # 後備方案（伺服器不支援 MOVE）：**冪等** copy → 標刪 → UID EXPUNGE（C1）。
        # 重試前先判前次進度，避免「COPY 後斷線重試 → 重複複本」：
        if not self._uid_present(uid):
            return  # 來源已無此 uid → 前次已完整搬走（no-op，重試安全）
        if not self._dest_has_copy(uid, dest_folder, mailbox):
            typ, _ = self._conn.uid("copy", uid, dest_folder)
            if typ != "OK":
                raise BackendError(
                    f"搬移失敗：COPY 未成功（{typ}），已中止且未刪除來源郵件"
                    f"（uid={uid} → {dest_folder}）。請確認目標資料夾後重試。"
                )
        self._conn.uid("store", uid, "+FLAGS", "(\\Deleted)")
        typ, _ = self._conn.uid("expunge", uid)  # UID EXPUNGE：只清這封
        if typ != "OK":
            # 伺服器無 UIDPLUS：別無選擇只能整夾 EXPUNGE（此後備路徑仍可能波及其他已標刪郵件）。
            self._conn.expunge()

    def _uid_present(self, uid: str) -> bool:
        """來源（目前選取夾）是否仍有此 UID（後備搬移冪等的快路徑判定）。"""
        typ, data = self._conn.uid("search", None, "UID", uid)  # type: ignore[arg-type]
        return typ == "OK" and bool(data) and bool(data[0]) and uid.encode() in data[0].split()

    def _message_id(self, uid: str) -> str | None:
        """取來源該 UID 的 Message-ID（無則 None）。"""
        typ, data = self._conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        if typ != "OK" or not data:
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2 and part[1]:
                mid = email.message_from_bytes(part[1]).get("Message-ID")
                if mid:
                    return mid.strip()
        return None

    def _dest_has_copy(self, uid: str, dest_folder: str, mailbox: str) -> bool:
        """後備搬移冪等（C1）：以 `Message-ID` 偵測目標夾是否已有此封（前次 COPY 殘留），避免重複複本。
        無 `Message-ID` → 回 False（盡力 COPY，已知殘留）。查畢切回來源夾（讀寫）續做標刪/expunge。"""
        mid = self._message_id(uid)
        if not mid:
            return False
        typ, _ = self._conn.select(dest_folder, readonly=True)
        self._selected = (dest_folder, True) if typ == "OK" else None
        try:
            if typ != "OK":
                return False  # 目標夾無法選取（如不存在）→ 視為無複本，交由後續 COPY（會得 TRYCREATE）
            typ, data = self._conn.uid("search", None, "HEADER", "Message-ID", mid)  # type: ignore[arg-type]
            return typ == "OK" and bool(data) and bool(data[0]) and bool(data[0].split())
        finally:
            self._ensure_selected(mailbox)  # 切回來源（讀寫）續做 COPY/標刪/expunge

    def move_many(
        self, uids: list[str], dest_folder: str, mailbox: str = "INBOX"
    ) -> dict[str, str | None]:
        """批次搬移多封（同來源夾→同目標夾）。回傳 ``{uid: None 成功 / 錯誤訊息 失敗}``。

        以 ``UID MOVE <uid 集合>`` 批次（超過 ``config.MOVE_BATCH_MAX`` 分塊），免重複 SELECT。
        批次未成功（伺服器不支援 MOVE 或拒絕）→ 對該塊**退回逐封** `_move_impl` 以精確歸因，
        單封失敗不連坐同批其他封。連線中斷透明重連並重試整批（UID MOVE 冪等、後備路徑亦冪等）；
        連線層級失敗（重連用盡）與 `ReauthRequired` 往外拋（不計入單列失敗）。
        """
        return self._with_reconnect(lambda: self._move_many_impl(list(uids), dest_folder, mailbox))

    def _move_many_impl(
        self, uids: list[str], dest_folder: str, mailbox: str
    ) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        for batch in _chunked(uids, config.MOVE_BATCH_MAX):
            self._ensure_selected(mailbox)
            typ, _ = self._conn.uid("move", ",".join(batch), dest_folder)
            if typ == "OK":
                for u in batch:
                    results[u] = None
                continue
            # 批次未成功 → 退回逐封以精確歸因（單封 _move_impl 含後備路徑）
            for u in batch:
                try:
                    self._move_impl(u, dest_folder, mailbox)
                    results[u] = None
                except ReauthRequired:
                    raise
                except Exception as exc:
                    if self._is_session_lost(exc):
                        raise  # 連線層級 → 交由 _with_reconnect 重連重試整批
                    results[u] = str(exc)
        return results

    def mark_read(self, uid: str, mailbox: str = "INBOX") -> None:
        self._ensure_selected(mailbox)
        self._conn.uid("store", uid, "+FLAGS", "(\\Seen)")

    def flag(self, uid: str, mailbox: str = "INBOX") -> None:
        self._ensure_selected(mailbox)
        self._conn.uid("store", uid, "+FLAGS", "(\\Flagged)")
