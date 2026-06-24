"""FakeIMAPConn —— 忠實的 IMAP 連線模擬器（測試基礎設施）。

目標：盡可能 **1:1 對齊 `imaplib.IMAP4` 的介面與「回應資料結構」**，並對每一道收到的
IMAP 指令留下動作日誌（command action log），讓驗證程序能查核：我們實際送出的 IMAP
指令、參數、順序是否正確、安全、符合應用規格。

設計鐵則（使這個模擬器能抓到「真 bug」的關鍵；新增 IMAP 功能時務必沿用同樣的忠實度）：
  * **FETCH 只回傳「你有索取」的 data items** —— 沒索取 `UID` 就不會有 `UID`。
    （這正是 0.5.1 hotfix 修掉的致命 bug 的觸發條件：假後端若自行塞 UID 就會遮蔽它。）
  * **EXPUNGE 清掉「選取信箱中所有被標 \\Deleted 的郵件」**（不只目標那封）。
    `UID EXPUNGE`（RFC 4315 UIDPLUS）才只清指定 UID。
  * **COPY / MOVE 目標夾不存在 → 回 `NO`（含 `[TRYCREATE]`）**，可驅動 fallback 與資料遺失測試。
  * **回應資料結構與 imaplib 完全一致**：FETCH 帶 literal 時回 `[(metadata_bytes, literal_bytes), b')']`；
    SEARCH 回 `('OK', [b'1 2 3'])`；LIST 回 `('OK', [b'(\\\\HasNoChildren) "/" "INBOX"', ...])`。

底層是真正的狀態機（信箱 → 郵件 → UID/旗標），COPY/MOVE 進目標夾會配發**新的 UID**
（符合 IMAP 語意：UID 在各信箱內唯一、不跨夾沿用）。

回應格式保真度已用「真正的 imaplib 解析迴圈」對拍確認（見 ``tests/test_imap_fidelity.py``，
以 ``tests/imaplib_probe.py`` 把 RFC 3501 wire bytes 餵進真 imaplib，斷言本模擬器輸出與其
**逐位元組相同**）：
  * FETCH 帶 literal → ``[(b'<seq> (<items> {len}', b'<literal>'), b')']``（注意 ``tuple[0]`` 不含 ``FETCH``）。
  * SEARCH → ``('OK', [b'<space-joined-uids>'])``。
  * LIST → ``('OK', [b'(\\HasNoChildren) "<sep>" "<mutf7-name>"'])``（CJK 夾名為 modified-UTF-7）。

雙層驗證：第一層比對指令動作日誌 ``log``（送出的 IMAP 指令/參數/順序是否符合規格）；
第二層比對 ``snapshot()`` 前後的資料變動是否合理。測試一律從 ``imap_dataset.fresh_sim()``
複製一份完整母版資料集出發。
"""
from __future__ import annotations

import base64
import imaplib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DELETED = "\\Deleted"  # 實際字串為 \Deleted（單一反斜線）
SEEN = "\\Seen"
FLAGGED = "\\Flagged"


@dataclass
class ImapCommand:
    """一道送進模擬器的 IMAP 指令紀錄（供驗證查核送出的指令是否正確、安全）。"""

    name: str                       # 'select' / 'uid' / 'list' / 'expunge' / 'create' / 'authenticate' / 'logout'
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)

    def __repr__(self) -> str:  # 便於 log 直接閱讀
        inside = ", ".join(repr(a) for a in self.args)
        if self.kwargs:
            inside += ", " + ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        return f"{self.name}({inside})"


@dataclass
class SimMessage:
    """模擬器底層的一封郵件。"""

    uid: int
    fields: dict[str, str] = field(default_factory=dict)  # 表頭名（大寫）-> 值
    flags: set[str] = field(default_factory=set)


def message(
    uid: int,
    subject: str = "",
    sender: str = "",
    to: str = "",
    date: str = "",
    *,
    flags: Optional[set[str]] = None,
) -> SimMessage:
    """建構一封模擬郵件的便捷函式。"""
    return SimMessage(
        uid,
        {"SUBJECT": subject, "FROM": sender, "TO": to, "DATE": date},
        set(flags or set()),
    )


def _unquote(name: str) -> str:
    """imaplib 會自動為含特殊字元的信箱名加引號；模擬器收端去引號還原。"""
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        return name[1:-1]
    return name


def _encode_mutf7(name: str) -> str:
    """資料夾名稱編成 IMAP modified-UTF-7（RFC 3501 §5.1.3），與真實伺服器 LIST 回應一致。

    對應產品端 ``imap_client._decode_mutf7`` 的逆運算；非 ASCII 連續段以 UTF-16BE +
    modified-BASE64（``/``→``,``、去 ``=`` padding）包在 ``&...-`` 內，``&`` 自身寫成 ``&-``。
    """
    out: list[str] = []
    i, n = 0, len(name)
    while i < n:
        ch = name[i]
        if ch == "&":
            out.append("&-")
            i += 1
        elif 0x20 <= ord(ch) <= 0x7E:
            out.append(ch)
            i += 1
        else:
            j = i
            while j < n and not (0x20 <= ord(name[j]) <= 0x7E):
                j += 1
            b64 = base64.b64encode(name[i:j].encode("utf-16-be")).decode("ascii")
            out.append("&" + b64.rstrip("=").replace("/", ",") + "-")
            i = j
    return "".join(out)


def _parse_uidset(spec: Any) -> list[int]:
    """解析 UID 集合：支援 '10'、'10,11,12'、'10:12'（含端點）。"""
    s = spec.decode() if isinstance(spec, (bytes, bytearray)) else str(spec)
    out: list[int] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            lo, hi = part.split(":", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


_HEADER_TITLE = {"SUBJECT": "Subject", "FROM": "From", "TO": "To", "DATE": "Date"}


def _encode_header_value(value: str) -> str:
    """ASCII 直接輸出；含非 ASCII → RFC 2047 ``=?UTF-8?B?...?=``（如真實郵件表頭）。

    模擬器 FETCH 表頭 literal 的**單一可信編碼器**——``FakeIMAPConn`` 與 ``imap_server.ImapServer``
    共用此函式（消除 P2 SR C3 點名的重複實作/漂移）。
    """
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def _render_header_literal(m: "SimMessage", section: str) -> bytes:
    """產生 ``BODY[HEADER.FIELDS (...)]`` 的 literal：依索取欄位輸出、結尾空行（單一可信來源）。

    非 ASCII 值以 RFC 2047 encoded-word 編碼（真實郵件即如此存放，非裸 UTF-8），確保產品端解碼
    路徑 ``_decode`` 被真實位元組流驅動；空值欄位（如空主旨）略過不輸出。
    """
    fm = re.search(r"HEADER\.FIELDS\s*\(([^)]*)\)", section, re.IGNORECASE)
    names = fm.group(1).split() if fm else list(m.fields.keys())
    lines = []
    for raw in names:
        key = raw.upper()
        if key in m.fields and m.fields[key]:
            title = _HEADER_TITLE.get(key, raw)
            lines.append(f"{title}: {_encode_header_value(m.fields[key])}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


class FakeIMAPConn:
    """模擬 ``imaplib.IMAP4_SSL`` 之介面（僅 OutlookIMAPClient 用到的子集，但回應忠實）。

    參數
    ----
    mailboxes : dict[str, list[SimMessage]]
        初始信箱與郵件。
    supports_move : bool
        伺服器是否支援 ``UID MOVE``。False → ``uid('move',...)`` 回 NO，驅動 copy/expunge fallback。
    supports_uidplus : bool
        是否支援 UIDPLUS（``UID EXPUNGE``）。False → ``uid('expunge',...)`` 回 NO，驅動整夾 EXPUNGE fallback。
    drop_uid : bool
        模擬「不守規矩的伺服器」：即使有索取 UID，FETCH 回應也不含 UID（用於測試上層的防線）。
    """

    def __init__(
        self,
        mailboxes: Optional[dict[str, list[SimMessage]]] = None,
        *,
        sep: str = "/",
        supports_move: bool = True,
        supports_uidplus: bool = True,
        drop_uid: bool = False,
        fail_fetch: bool = False,
    ) -> None:
        self.mailboxes: dict[str, list[SimMessage]] = {
            k: list(v) for k, v in (mailboxes or {"INBOX": []}).items()
        }
        self._sep = sep
        self._supports_move = supports_move
        self._supports_uidplus = supports_uidplus
        self._drop_uid = drop_uid
        self._fail_fetch = fail_fetch
        self._uidnext: dict[str, int] = {
            name: (max((m.uid for m in msgs), default=0) + 1)
            for name, msgs in self.mailboxes.items()
        }
        self._selected: Optional[str] = None
        self._readonly = False
        self.log: list[ImapCommand] = []
        self.auth_string: Optional[bytes] = None  # connect() 送出的 XOAUTH2 認證字串
        # ── token 過期 / session 失效模擬（擬真 Outlook 的「中途過期 → EOF 連環」）──
        self._session_valid = True
        self._expire_arm: Optional[tuple[str, int]] = None  # (op_kind, nth) 一次性
        self._op_counts: dict[str, int] = {}
        self._persist_invalid = False  # True：失效後即使重新認證也不恢復（伺服器持續不可用）

    # ---------- 失效模擬（token 過期 / 連線中斷）----------
    def arm_expiry(self, *, before_op: str, nth: int = 1, persist: bool = False) -> None:
        """安排「第 nth 次 ``before_op`` 操作時 session 失效」（一次性）。

        失效後所有指令擲 ``imaplib.IMAP4.abort``（含 AccessTokenExpired 標記，擬真 Outlook
        token 過期 → session 作廢 → 後續 EOF 連環）；直到再次 ``authenticate`` 才恢復。
        ``persist=True``：失效後即使重新認證也不恢復（模擬伺服器持續不可用 → 重連用盡仍失敗）。
        """
        self._expire_arm = (before_op.upper(), nth)
        self._persist_invalid = persist

    def _arm_tick(self, op_kind: str) -> None:
        self._op_counts[op_kind] = self._op_counts.get(op_kind, 0) + 1
        if self._expire_arm and op_kind == self._expire_arm[0] and self._op_counts[op_kind] == self._expire_arm[1]:
            self._session_valid = False
            self._expire_arm = None  # 一次性

    def _check_valid(self) -> None:
        if not self._session_valid:
            raise imaplib.IMAP4.abort(
                "command: SELECT => Session invalidated - AccessTokenExpired"
            )

    # ---------- 第二層驗證：資料狀態快照 ----------
    def snapshot(self) -> dict[str, list[tuple[int, frozenset]]]:
        """各信箱狀態的深拷貝快照（uid + 旗標集合），供測試前後比對「資料變動是否合理」。

        雙層確認之第二層（第一層為指令動作日誌 ``log``）。
        """
        return {
            name: [(m.uid, frozenset(m.flags)) for m in msgs]
            for name, msgs in self.mailboxes.items()
        }

    # ---------- 動作日誌查詢輔助 ----------
    def commands(self, name: str) -> list[ImapCommand]:
        return [c for c in self.log if c.name == name]

    def uid_commands(self, sub: str) -> list[ImapCommand]:
        """取出特定 UID 子指令（'fetch'/'move'/'copy'/'store'/'expunge'/'search'）的紀錄。"""
        sub = sub.upper()
        return [c for c in self.log if c.name == "uid" and str(c.args[0]).upper() == sub]

    # ---------- 連線/認證 ----------
    def authenticate(self, mechanism: str, authobject: Callable[[bytes], bytes]) -> tuple:
        self.log.append(ImapCommand("authenticate", (mechanism,)))
        try:
            # imaplib 會以挑戰呼叫 authobject 取得回應字串；保存以供驗證 XOAUTH2 格式。
            self.auth_string = authobject(b"")
        except Exception:
            self.auth_string = None
        if not self._persist_invalid:
            self._session_valid = True  # 成功（重新）認證 → session 恢復（persist 模式則維持失效）
        return ("OK", [b"AUTHENTICATE completed"])

    def logout(self) -> tuple:
        self.log.append(ImapCommand("logout"))
        self._selected = None
        return ("BYE", [b"LOGOUT Requested"])

    # ---------- 資料夾 ----------
    def list(self, directory: str = '""', pattern: str = "*") -> tuple:
        self.log.append(ImapCommand("list", (directory, pattern)))
        self._check_valid()
        # 與真 imaplib 一致：每行 b'(\\HasNoChildren) "<sep>" "<mutf7-name>"'（已剝除 '* LIST '）。
        lines = [
            f'(\\HasNoChildren) "{self._sep}" "{_encode_mutf7(name)}"'.encode()
            for name in self.mailboxes
        ]
        return ("OK", lines)

    def create(self, mailbox: str) -> tuple:
        self.log.append(ImapCommand("create", (mailbox,)))
        self._check_valid()
        name = _unquote(mailbox)
        if name in self.mailboxes:
            return ("NO", [b"[ALREADYEXISTS] Mailbox already exists"])
        self.mailboxes[name] = []
        self._uidnext[name] = 1
        return ("OK", [b"CREATE completed"])

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple:
        self.log.append(ImapCommand("select", (mailbox,), {"readonly": readonly}))
        self._check_valid()
        name = _unquote(mailbox)
        if name not in self.mailboxes:
            return ("NO", [f"[NONEXISTENT] Mailbox doesn't exist: {name}".encode()])
        self._selected = name
        self._readonly = readonly
        return ("OK", [str(len(self.mailboxes[name])).encode()])

    # ---------- 整夾 EXPUNGE ----------
    def expunge(self) -> tuple:
        self.log.append(ImapCommand("expunge"))
        self._check_valid()
        msgs = self._sel_msgs()
        return self._do_expunge([m.uid for m in msgs if DELETED in m.flags])

    # ---------- UID 指令分派 ----------
    def uid(self, command: str, *args: Any) -> tuple:
        self.log.append(ImapCommand("uid", (command, *args)))
        cmd = command.upper()
        self._arm_tick(cmd)   # 可能在此引發 session 失效（第 nth 次該操作）
        self._check_valid()
        if cmd == "SEARCH":
            return self._uid_search(args)
        if cmd == "FETCH":
            return self._uid_fetch(args[0], args[1])
        if cmd == "MOVE":
            return self._uid_move(args[0], args[1])
        if cmd == "COPY":
            return self._uid_copy(args[0], args[1])
        if cmd == "STORE":
            return self._uid_store(args[0], args[1], args[2])
        if cmd == "EXPUNGE":
            return self._uid_expunge(args[0])
        raise AssertionError(f"FakeIMAPConn 尚未模擬的 UID 指令：{command}")

    # ---------- 內部：選取狀態 ----------
    def _require_selected(self) -> str:
        if self._selected is None:
            raise AssertionError("尚未 SELECT 任何信箱")
        return self._selected

    def _sel_msgs(self) -> list[SimMessage]:
        return self.mailboxes[self._require_selected()]

    def _seq_of(self, msg: SimMessage) -> int:
        return self._sel_msgs().index(msg) + 1  # IMAP 序號為 1 起

    # ---------- UID SEARCH ----------
    def _uid_search(self, args: tuple) -> tuple:
        msgs = self._sel_msgs()
        joined = b" ".join(str(m.uid).encode() for m in msgs)
        return ("OK", [joined])

    # ---------- UID FETCH（忠實：只回索取的 data items）----------
    def _uid_fetch(self, uidset: Any, items: str) -> tuple:
        if self._fail_fetch:
            return ("NO", None)  # 模擬批次 FETCH 失敗（驅動上層大聲報錯、不靜默回不完整）
        want = set(_parse_uidset(uidset))
        msgs = [m for m in self._sel_msgs() if m.uid in want]

        uid_requested = bool(re.search(r"\bUID\b", items)) and not self._drop_uid
        flags_requested = bool(re.search(r"\bFLAGS\b", items))
        body_m = re.search(r"BODY(?:\.PEEK)?\[([^\]]*)\]", items)

        # 依「在請求字串中出現的順序」決定 data items 的回應順序（與真實伺服器一致）。
        order: list[tuple[int, str]] = []
        if uid_requested:
            order.append((re.search(r"\bUID\b", items).start(), "UID"))  # type: ignore[union-attr]
        if flags_requested:
            order.append((re.search(r"\bFLAGS\b", items).start(), "FLAGS"))  # type: ignore[union-attr]
        if body_m:
            order.append((body_m.start(), "BODY"))
        order.sort()
        seq_tokens = [tok for _, tok in order]

        out: list[Any] = []
        for m in msgs:
            prefix: list[str] = []
            suffix: list[str] = []
            literal: Optional[bytes] = None
            hit_body = False
            for tok in seq_tokens:
                if tok == "UID":
                    piece = f"UID {m.uid}"
                    (suffix if hit_body else prefix).append(piece)
                elif tok == "FLAGS":
                    piece = f"FLAGS ({' '.join(sorted(m.flags))})"
                    (suffix if hit_body else prefix).append(piece)
                elif tok == "BODY":
                    section = body_m.group(1)  # 回應回 BODY[...]（去掉 .PEEK）
                    literal = self._render_header(m, section)
                    prefix.append(f"BODY[{section}] {{{len(literal)}}}")
                    hit_body = True
            seq = self._seq_of(m)
            head = f"{seq} (" + " ".join(prefix)
            if literal is not None:
                closer = (" " + " ".join(suffix) if suffix else "") + ")"
                out.append((head.encode(), literal))
                out.append(closer.encode())
            else:
                line = head + (" " + " ".join(suffix) if suffix else "") + ")"
                out.append(line.encode())
        return ("OK", out)

    def _render_header(self, m: SimMessage, section: str) -> bytes:
        """產生 BODY[HEADER.FIELDS (...)] 的 literal（委派模組級單一可信來源 _render_header_literal）。"""
        return _render_header_literal(m, section)

    @staticmethod
    def _encode_value(value: str) -> str:
        """相容保留：委派模組級 _encode_header_value（單一可信來源）。"""
        return _encode_header_value(value)

    # ---------- UID MOVE / COPY / STORE / EXPUNGE ----------
    def _find(self, mailbox: str, uid: int) -> Optional[SimMessage]:
        for m in self.mailboxes.get(mailbox, []):
            if m.uid == uid:
                return m
        return None

    def _append_copy(self, dest: str, src_msg: SimMessage) -> SimMessage:
        new_uid = self._uidnext.get(dest, 1)
        self._uidnext[dest] = new_uid + 1
        copy = SimMessage(new_uid, dict(src_msg.fields), set())  # 新夾、新 UID、旗標不沿用
        self.mailboxes.setdefault(dest, []).append(copy)
        return copy

    def _uid_move(self, uid: Any, dest: Any) -> tuple:
        if not self._supports_move:
            return ("NO", [b"MOVE not supported"])
        src = self._require_selected()
        dest_name = _unquote(dest.decode() if isinstance(dest, bytes) else str(dest))
        if dest_name not in self.mailboxes:
            return ("NO", [b"[TRYCREATE] Mailbox doesn't exist"])
        m = self._find(src, int(uid))
        if m is None:
            return ("NO", [b"No matching message"])
        self.mailboxes[src].remove(m)
        self._append_copy(dest_name, m)
        return ("OK", [b"[COPYUID] (move completed)"])

    def _uid_copy(self, uid: Any, dest: Any) -> tuple:
        src = self._require_selected()
        dest_name = _unquote(dest.decode() if isinstance(dest, bytes) else str(dest))
        if dest_name not in self.mailboxes:
            return ("NO", [b"[TRYCREATE] Mailbox doesn't exist"])
        m = self._find(src, int(uid))
        if m is None:
            return ("NO", [b"No matching message"])
        self._append_copy(dest_name, m)
        return ("OK", [b"[COPYUID] (copy completed)"])

    def _uid_store(self, uid: Any, op: str, flags: str) -> tuple:
        m = self._find(self._require_selected(), int(uid))
        if m is None:
            return ("NO", [b"No matching message"])
        parsed = set(re.findall(r"\\?\w+", flags.strip("()")))
        # findall 會把 '\Deleted' 抓成 'Deleted'；補回反斜線旗標
        parsed = {f if f.startswith("\\") else "\\" + f for f in parsed}
        if op.upper().startswith("+"):
            m.flags |= parsed
        elif op.upper().startswith("-"):
            m.flags -= parsed
        else:
            m.flags = parsed
        return ("OK", [f"{self._seq_of(m)} (FLAGS ({' '.join(sorted(m.flags))}))".encode()])

    def _uid_expunge(self, uidset: Any) -> tuple:
        if not self._supports_uidplus:
            return ("NO", [b"UIDPLUS not supported"])
        targets = set(_parse_uidset(uidset))
        msgs = self._sel_msgs()
        return self._do_expunge([m.uid for m in msgs if m.uid in targets and DELETED in m.flags])

    def _do_expunge(self, uids: list[int]) -> tuple:
        sel = self._require_selected()
        removed_seqs: list[bytes] = []
        for uid in uids:
            m = self._find(sel, uid)
            if m is not None:
                removed_seqs.append(str(self._seq_of(m)).encode())
                self.mailboxes[sel].remove(m)
        return ("OK", removed_seqs)


def client_on(sim: FakeIMAPConn) -> Any:
    """把模擬器接成一個**真實的** ``OutlookIMAPClient``（``_imap`` 已就緒，無需 connect）。

    契約／資料集／保真度測試的單一入口：一律以「真實 client 跑在模擬器上」驗證，
    而非用任意假物件替身。
    """
    from mailkeeper.imap_client import OutlookIMAPClient

    c = OutlookIMAPClient("user@x.com", "tok")  # 跑 __init__ 設好韌性屬性
    c._imap = sim  # 直接掛上模擬器（不經 connect）；`_conn` property 讀的就是 `_imap`
    return c


def install(monkeypatch: Any, sim: FakeIMAPConn, *, capture: Optional[dict] = None) -> dict:
    """把 ``imaplib.IMAP4_SSL`` 換成「回傳此模擬器」的假類別。

    讓**真實的** ``OutlookIMAPClient.connect()`` 跑在模擬器之上（而非用任意假 client 替身），
    並把建構參數（host/port/timeout）記到回傳的 dict，供逾時/連線測試查核。
    這是「所有 Outlook IMAP 連線在測試中一律走 FakeIMAPConn」的統一接點。
    """
    cap: dict = capture if capture is not None else {}
    cap.setdefault("constructed", 0)

    def factory(host: Any, port: Any, timeout: Any = None) -> FakeIMAPConn:
        cap["host"] = host
        cap["port"] = port
        cap["timeout"] = timeout
        cap["constructed"] += 1
        return sim

    monkeypatch.setattr("mailkeeper.imap_client.imaplib.IMAP4_SSL", factory)
    return cap


def connected_client(monkeypatch: Any, sim: FakeIMAPConn, **client_kw: Any) -> Any:
    """install() + 建構真實 ``OutlookIMAPClient`` + connect()，回傳已連線 client。

    用於需要「重連」的測試：`_with_reconnect` 會重建 `IMAP4_SSL`，install 的工廠讓它取回**同一個**
    模擬器（於是 `authenticate` 再次被呼叫、session 恢復）。`client_kw` 透傳如 `token_provider`/`on_status`。
    """
    from mailkeeper.imap_client import OutlookIMAPClient

    install(monkeypatch, sim)
    c = OutlookIMAPClient("user@x.com", "tok", **client_kw)
    c.connect()
    return c
