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
from dataclasses import dataclass
from email.header import decode_header
from typing import Any, Callable, Iterator

import charset_normalizer

from . import config

# 後端中立的錯誤別名：讓上層 (cli) 不必直接 import imaplib，維持 seam 純度。
BackendError = imaplib.IMAP4.error

_FETCH_BATCH = 50  # 每批 UID FETCH 的封數：減少往返、並讓進度於下載期間分批前進
_UID_RE = re.compile(rb"UID (\d+)")


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
    ) -> None:
        self._email = email_account
        self._token = access_token
        self._host = host if host is not None else config.IMAP_HOST
        self._port = port if port is not None else config.IMAP_PORT
        self._timeout = timeout if timeout is not None else config.IMAP_TIMEOUT
        self._imap: imaplib.IMAP4_SSL | None = None

    # ---------- 連線管理 ----------
    def connect(self) -> None:
        self._imap = imaplib.IMAP4_SSL(self._host, self._port, timeout=self._timeout)
        # XOAUTH2 認證字串格式 (注意是 \x01 控制字元，不是空白)
        auth_string = f"user={self._email}\x01auth=Bearer {self._token}\x01\x01"
        self._imap.authenticate("XOAUTH2", lambda _: auth_string.encode())

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

    # ---------- 讀取 ----------
    def list_folders(self) -> list[str]:
        """列舉信箱所有資料夾名稱。"""
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
        進度 `on_progress(done, total)`，使大量郵件下載期間可見進展、不像當機。"""
        self._conn.select(folder, readonly=True)
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
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])",
            )
            if typ != "OK":
                # 批次失敗不可靜默吞（會回傳不完整標頭、誤導後續分類）：大聲報錯。
                raise BackendError(f"讀取標頭失敗（{typ}）：{folder} 的批次 FETCH 未成功，請重試。")
            if msg_data:
                for part in msg_data:
                    if not isinstance(part, tuple) or len(part) < 2 or part[1] is None:
                        continue
                    msg = email.message_from_bytes(part[1])
                    headers.append(
                        MailHeader(
                            uid=_extract_uid(part[0]),
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

    # ---------- 整理動作 ----------
    def ensure_folder(self, folder: str) -> None:
        """確保資料夾存在 (已存在會回 NO，直接忽略即可)。"""
        self._conn.create(folder)

    def move(self, uid: str, dest_folder: str, mailbox: str = "INBOX") -> None:
        """將郵件搬到指定資料夾。Outlook 支援 UID MOVE 擴充。"""
        self._conn.select(mailbox)
        typ, _ = self._conn.uid("move", uid, dest_folder)
        if typ != "OK":
            # 後備方案：不支援 MOVE 時改用 copy + delete + expunge
            self._conn.uid("copy", uid, dest_folder)
            self._conn.uid("store", uid, "+FLAGS", "(\\Deleted)")
            self._conn.expunge()

    def mark_read(self, uid: str, mailbox: str = "INBOX") -> None:
        self._conn.select(mailbox)
        self._conn.uid("store", uid, "+FLAGS", "(\\Seen)")

    def flag(self, uid: str, mailbox: str = "INBOX") -> None:
        self._conn.select(mailbox)
        self._conn.uid("store", uid, "+FLAGS", "(\\Flagged)")
