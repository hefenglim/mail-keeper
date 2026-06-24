"""ImapServer —— 線級（wire-level）的記憶體 IMAP 伺服器狀態引擎（方案 B 核心，P1 讀取路徑）。

定位（為何是「最堅實」的離線測試地基）：
  * 上層產品跑的是**真正的** ``imaplib.IMAP4_SSL``（見 ``imap_transport.SimIMAP4_SSL``），
    本引擎只在 socket 位置提供「位元組進、位元組出」的伺服器。於是命令組裝、literal 讀取、
    狀態機、錯誤包裝、CAPABILITY 交握、AUTHENTICATE 續傳**全部由真 imaplib 執行**，
    保真度自動且完整、零漂移——不再倚賴高階假物自行臆造回應結構。
  * ``feed(line)`` 收一條 imaplib 送來的命令列（已去 CRLF），執行 handler，序列化回**真實伺服器
    會送的 wire bytes**（untagged 行 + literal + tagged ``OK/NO/BAD``）。

最大化驗證數據（供邏輯正確性 + loop regression / 效能瓶頸分析）：
  1. 原始 wire transcript：``wire_in`` / ``wire_out``（可重播、可 diff）。
  2. 結構化命令 log：``log: list[ServerOp]``（seq/tag/command/args/mailbox/affected_uids/
     result_typ/response_code/t_wall）——比舊 ``ImapCommand`` 多了 tag/影響 UID/結果碼/時間。
  3. 狀態快照 ``snapshot()``：各夾 ``(uid, frozenset(flags))``，前後比對資料變動。
  4. 分析助手：``command_count`` / ``fetch_count`` / ``assert_all_fetches_request_uid``
     （釘死 0.5.x「FETCH 未索取 UID」致命回歸類）/ ``dump()``（失敗時一次吐 transcript+log+快照）。

P1 範圍（讀取路徑）：greeting + ``[CAPABILITY]`` 交握、AUTHENTICATE XOAUTH2 續傳、NOOP、
SELECT/EXAMINE（FLAGS/EXISTS/RECENT/UIDVALIDITY/UIDNEXT + ``[READ-ONLY]/[READ-WRITE]``）、
LIST（modified-UTF-7）、UID SEARCH ALL、UID FETCH ``(UID BODY.PEEK[HEADER.FIELDS (...)])``（literal）、
LOGOUT。破壞性命令（CREATE/UID MOVE/COPY/STORE/EXPUNGE）+ 傳輸層失效注入於 P2 加入
（已留 handler 分派點）。

資料模型沿用 ``imap_sim`` 的 ``SimMessage`` 與編碼助手（modified-UTF-7 / UID 集合解析），
與既有母版資料集（``imap_dataset``）共用同一套郵件物件——單一可信來源、不分岔。
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

# 沿用既有、已對拍真 imaplib 驗證過的資料模型與編碼助手（單一可信來源）。
from imap_sim import (  # noqa: F401  (DELETED/SEEN 供 P2 與測試引用)
    DELETED,
    SEEN,
    SimMessage,
    _encode_mutf7,
    _parse_uidset,
    _render_header_literal,  # 單一可信來源：FETCH 表頭 literal 序列化（與 FakeIMAPConn 共用，消 C3 漂移）
    _unquote,
    message,
)

CRLF = b"\r\n"


# ── 結構化命令 log（驗證與效能分析的主要數據面）─────────────────────────────

@dataclass
class ServerOp:
    """伺服器引擎執行過的一道命令（比舊 ImapCommand 多 tag/影響 UID/結果碼/時間）。"""

    seq: int
    tag: str
    command: str                    # 'CAPABILITY'/'AUTHENTICATE'/'SELECT'/'EXAMINE'/'LIST'/'UID SEARCH'/'UID FETCH'/'LOGOUT'…
    args: tuple = ()
    mailbox: Optional[str] = None
    affected_uids: tuple = ()
    result_typ: str = "OK"          # 'OK'/'NO'/'BAD'
    response_code: Optional[str] = None  # 如 'READ-ONLY'/'NONEXISTENT'/'TRYCREATE'…
    t_wall: float = 0.0

    def __repr__(self) -> str:  # 便於 dump 直接閱讀
        extra = f" mbox={self.mailbox}" if self.mailbox else ""
        uids = f" uids={list(self.affected_uids)}" if self.affected_uids else ""
        code = f" [{self.response_code}]" if self.response_code else ""
        return f"#{self.seq} {self.tag} {self.command} -> {self.result_typ}{code}{extra}{uids}"


class ImapServer:
    """有狀態的記憶體 IMAP 伺服器引擎（位元組進、位元組出）。

    參數
    ----
    mailboxes : dict[str, list[SimMessage]]
        初始信箱與郵件（沿用母版資料集；引擎只持有參考，測試請從 ``fresh`` 複本出發）。
    sep : str
        階層分隔字元（LIST 回應用）。
    supports_move / supports_uidplus : bool
        伺服器能力旗標（影響 CAPABILITY 與 P2 的 MOVE/UID EXPUNGE 行為）。
    """

    def __init__(
        self,
        mailboxes: Optional[dict[str, list[SimMessage]]] = None,
        *,
        sep: str = "/",
        supports_move: bool = True,
        supports_uidplus: bool = True,
    ) -> None:
        self.mailboxes: dict[str, list[SimMessage]] = {
            k: list(v) for k, v in (mailboxes or {"INBOX": []}).items()
        }
        self._sep = sep
        self._supports_move = supports_move
        self._supports_uidplus = supports_uidplus
        self._uidnext: dict[str, int] = {
            name: (max((m.uid for m in msgs), default=0) + 1)
            for name, msgs in self.mailboxes.items()
        }
        self._uidvalidity: dict[str, int] = {name: 1000 + i for i, name in enumerate(self.mailboxes)}

        # 狀態機（與 imaplib 對應）：NONAUTH → AUTH → SELECTED → LOGOUT。
        self._state = "NONAUTH"
        self._selected: Optional[str] = None
        self._readonly = False
        self._authenticated = False
        self._pending_sasl: Optional[tuple[bytes, str]] = None  # (tag, mechanism) 等待 SASL 續傳回應

        # 失效注入（P2，傳輸/協定層）：擬真 token 過期→session 作廢→EOF 連環，及 OSError/SSLError/BYE/authfail。
        self._alive = True
        self._socket_dead: Optional[str] = None  # None / 'oserror' / 'sslerror'（transport 讀取時據此 raise；'eof' 由空緩衝自然 EOF）
        self._arm: Optional[tuple[str, int, str, bool]] = None  # (op_kind, nth, mode, persist)
        self._op_counts: dict[str, int] = {}

        # 驗證數據面。
        self.wire_in: list[bytes] = []
        self.wire_out: list[bytes] = []
        self.log: list[ServerOp] = []
        self.auth_string: Optional[bytes] = None  # AUTHENTICATE 解出的 XOAUTH2 認證字串
        self._seq = 0

    # ---------- 能力 / 招呼 ----------
    def _capabilities(self) -> list[str]:
        caps = ["IMAP4rev1"]
        if self._supports_uidplus:
            caps.append("UIDPLUS")
        if self._supports_move:
            caps.append("MOVE")
        caps.append("AUTH=XOAUTH2")
        return caps

    def greeting(self) -> bytes:
        """連線招呼（由傳輸層在 open() 時注入緩衝，imaplib `_connect` 會讀它判定 NONAUTH）。

        每條新連線都呼叫一次 → 重置「連線存活 / socket 失效」旗標（重連後的新 session 恢復健康，
        但 `_op_counts` 不重置：一次性 arm 不再觸發、persist arm 於重連後該操作仍再次觸發）。
        """
        self._alive = True
        self._socket_dead = None
        self._pending_sasl = None
        g = f"* OK [CAPABILITY {' '.join(self._capabilities())}] MailKeeper IMAP simulator ready".encode() + CRLF
        self.wire_out.append(g)
        return g

    # ---------- 主入口：收一條命令列，回序列化的 wire bytes ----------
    def feed(self, line: bytes) -> bytes:
        """收 imaplib 送出的「一條命令列」（已去 CRLF），執行並回真實伺服器會送的 wire bytes。"""
        self.wire_in.append(line)
        if self._pending_sasl is not None:
            resp = self._finish_authenticate(line)
        elif not self._alive:
            resp = b""  # 連線已失效（EOF 連環）：持續回空，直到用戶端重連（greeting 重置）
        else:
            failure = self._arm_check(self._op_kind(line))
            resp = self._trigger_failure(failure, line) if failure else self._dispatch(line)
        self.wire_out.append(resp)
        return resp

    def _dispatch(self, line: bytes) -> bytes:
        parts = line.split(b" ", 2)
        if len(parts) < 2:
            # 真 imaplib 必送「tag 命令」；走到這代表測試/引擎有 bug → 大聲失敗，絕不回會讓
            # `_get_tagged_response` 空轉的 untagged BAD（遞延自 P1 SR 的收斂條件）。
            raise AssertionError(f"引擎收到無 tag/命令的畸形行（真 imaplib 不該送出）：{line!r}")
        tag = parts[0]
        command = parts[1].upper().decode("ascii", "replace")
        rest = parts[2] if len(parts) > 2 else b""
        handler: Optional[Callable[[bytes, bytes], bytes]] = getattr(self, "_cmd_" + command.lower(), None)
        if handler is None:
            self._record(tag, command, (), None, (), "BAD", None)
            return self._tagged(tag, "BAD", f"{command} not supported by simulator")
        return handler(tag, rest)

    # ---------- 失效注入（P2）：擬真 token 過期 / 連線中斷 / 協定錯誤 ----------
    def arm_expiry(self, *, before_op: str, nth: int = 1, mode: str = "eof", persist: bool = False) -> None:
        """安排「第 nth 次 ``before_op`` 操作時 session 失效」（觸發前不執行該操作 → 不誤動資料）。

        ``mode``（一套注入即覆蓋產品 ``_is_session_lost`` 的全部真實入口）：
          * ``eof``      —— 伺服器靜默關閉連線：feed 回空 bytes → 真 imaplib readline 讀到 EOF →
                            ``abort('socket error: EOF')``（實測 Outlook token 過期的主路徑）。
          * ``oserror`` / ``sslerror`` —— 傳輸層 read 拋 OSError / ssl.SSLError（先前未測分支）。
          * ``bye``      —— 伺服器送 ``* BYE`` → imaplib ``_check_bye`` → abort。
          * ``authfail`` —— tagged ``BAD [AUTHENTICATIONFAILED]`` → imaplib 拋 error 含標記（先前未測）。
        ``persist=True``：每次（含重連後）該操作都再次失敗（→ 重連用盡仍失敗）；否則一次性
        （重連後即恢復）。``before_op``：命令名或 UID 子命令（如 'MOVE'/'FETCH'/'SELECT'）。
        """
        self._arm = (before_op.upper(), nth, mode, persist)

    def _op_kind(self, line: bytes) -> str:
        parts = line.split(b" ", 2)
        if len(parts) < 2:
            return ""
        cmd = parts[1].upper().decode("ascii", "replace")
        if cmd == "UID" and len(parts) > 2:
            return parts[2].split(b" ", 1)[0].upper().decode("ascii", "replace")
        return cmd

    def _arm_check(self, op_kind: str) -> Optional[str]:
        if op_kind:
            self._op_counts[op_kind] = self._op_counts.get(op_kind, 0) + 1
        if self._arm is None or not op_kind:
            return None
        a_op, a_nth, a_mode, a_persist = self._arm
        if op_kind != a_op:
            return None
        count = self._op_counts.get(op_kind, 0)
        if (count >= a_nth) if a_persist else (count == a_nth):
            if not a_persist:
                self._arm = None  # 一次性
            return a_mode
        return None

    def _trigger_failure(self, mode: str, line: bytes) -> bytes:
        tag = line.split(b" ", 1)[0]
        if mode in ("eof", "oserror", "sslerror"):
            self._alive = False
            self._socket_dead = None if mode == "eof" else mode
            return b""  # eof：空 inbuf → imaplib EOF abort；oserror/sslerror：transport read 時拋
        if mode == "bye":
            self._alive = False
            return self._untagged("BYE session terminated - AccessTokenExpired")
        if mode == "authfail":
            return self._tagged(tag, "BAD", "[AUTHENTICATIONFAILED] Session invalidated - AccessTokenExpired")
        raise AssertionError(f"未知失效注入模式：{mode}")

    def raise_if_socket_dead(self) -> None:
        """供傳輸層 read/readline 呼叫：oserror/sslerror 模式時拋對應例外（eof 由空緩衝自然 EOF）。"""
        if self._socket_dead == "oserror":
            raise OSError("simulated connection reset by peer")
        if self._socket_dead == "sslerror":
            import ssl

            raise ssl.SSLError("simulated SSL error during session")

    # ---------- handlers：連線 / 認證 ----------
    def _cmd_capability(self, tag: bytes, rest: bytes) -> bytes:
        self._record(tag, "CAPABILITY", (), None, (), "OK", None)
        return self._untagged(f"CAPABILITY {' '.join(self._capabilities())}") + self._tagged(
            tag, "OK", "CAPABILITY completed"
        )

    def _cmd_noop(self, tag: bytes, rest: bytes) -> bytes:
        self._record(tag, "NOOP", (), self._selected, (), "OK", None)
        return self._tagged(tag, "OK", "NOOP completed")

    def _cmd_authenticate(self, tag: bytes, rest: bytes) -> bytes:
        mech = rest.split(b" ", 1)[0].decode("ascii", "replace").upper()
        # 真 imaplib 會先讀到續傳 '+ '，再送出 base64 SASL 回應；此處回續傳並掛起該 tag。
        self._pending_sasl = (tag, mech)
        return b"+ " + CRLF

    def _finish_authenticate(self, line: bytes) -> bytes:
        tag, mech = self._pending_sasl  # type: ignore[misc]
        self._pending_sasl = None
        try:
            self.auth_string = base64.b64decode(line)
        except Exception:
            self.auth_string = None
        self._authenticated = True
        self._state = "AUTH"
        self._record(tag, "AUTHENTICATE", (mech,), None, (), "OK", None)
        return self._tagged(tag, "OK", "AUTHENTICATE completed")

    def _cmd_logout(self, tag: bytes, rest: bytes) -> bytes:
        self._state = "LOGOUT"
        self._selected = None
        self._record(tag, "LOGOUT", (), None, (), "OK", None)
        return self._untagged("BYE MailKeeper simulator logging out") + self._tagged(
            tag, "OK", "LOGOUT completed"
        )

    # ---------- handlers：選取 ----------
    def _cmd_select(self, tag: bytes, rest: bytes) -> bytes:
        return self._select_impl(tag, rest, readonly=False, verb="SELECT")

    def _cmd_examine(self, tag: bytes, rest: bytes) -> bytes:
        return self._select_impl(tag, rest, readonly=True, verb="EXAMINE")

    def _select_impl(self, tag: bytes, rest: bytes, *, readonly: bool, verb: str) -> bytes:
        name = _unquote(rest.decode("utf-8", "replace"))
        if name not in self.mailboxes:
            self._state = "AUTH"
            self._selected = None
            self._record(tag, verb, (name,), name, (), "NO", "NONEXISTENT")
            return self._tagged(tag, "NO", f"[NONEXISTENT] Mailbox doesn't exist: {name}")
        self._selected = name
        self._readonly = readonly
        self._state = "SELECTED"
        msgs = self.mailboxes[name]
        flags = r"\Answered \Flagged \Deleted \Seen \Draft"
        code = "READ-ONLY" if readonly else "READ-WRITE"
        body = (
            self._untagged(f"FLAGS ({flags})")
            + self._untagged(f"{len(msgs)} EXISTS")
            + self._untagged("0 RECENT")
            + self._untagged(f"OK [UIDVALIDITY {self._uidvalidity.get(name, 1)}] UIDs valid")
            + self._untagged(f"OK [UIDNEXT {self._uidnext.get(name, 1)}] Predicted next UID")
            + self._tagged(tag, "OK", f"[{code}] {verb} completed")
        )
        self._record(tag, verb, (name,), name, (), "OK", code)
        return body

    # ---------- handlers：CREATE（確保資料夾）----------
    def _cmd_create(self, tag: bytes, rest: bytes) -> bytes:
        name = _unquote(rest.decode("utf-8", "replace"))
        if name in self.mailboxes:
            self._record(tag, "CREATE", (name,), name, (), "NO", "ALREADYEXISTS")
            return self._tagged(tag, "NO", "[ALREADYEXISTS] Mailbox already exists")
        self.mailboxes[name] = []
        self._uidnext[name] = 1
        self._uidvalidity[name] = 2000 + len(self.mailboxes)
        self._record(tag, "CREATE", (name,), name, (), "OK", None)
        return self._tagged(tag, "OK", "CREATE completed")

    # ---------- handlers：整夾 EXPUNGE（fallback 用；清選取夾所有 \Deleted）----------
    def _cmd_expunge(self, tag: bytes, rest: bytes) -> bytes:
        uids = [m.uid for m in self._sel_msgs() if DELETED in m.flags]
        lines, removed = self._do_expunge(uids)
        self._record(tag, "EXPUNGE", (), self._selected, tuple(removed), "OK", None)
        return lines + self._tagged(tag, "OK", "EXPUNGE completed")

    # ---------- handlers：LIST ----------
    def _cmd_list(self, tag: bytes, rest: bytes) -> bytes:
        lines = b"".join(
            self._untagged(f'LIST (\\HasNoChildren) "{self._sep}" "{_encode_mutf7(name)}"')
            for name in self.mailboxes
        )
        self._record(tag, "LIST", ('""', "*"), None, tuple(self.mailboxes), "OK", None)
        return lines + self._tagged(tag, "OK", "LIST completed")

    # ---------- handlers：UID 分派 ----------
    def _cmd_uid(self, tag: bytes, rest: bytes) -> bytes:
        sub, _, tail = rest.partition(b" ")
        sub_u = sub.upper().decode("ascii", "replace")
        if sub_u == "SEARCH":
            return self._uid_search(tag, tail)
        if sub_u == "FETCH":
            return self._uid_fetch(tag, tail)
        if sub_u == "MOVE":
            return self._uid_move(tag, tail)
        if sub_u == "COPY":
            return self._uid_copy(tag, tail)
        if sub_u == "STORE":
            return self._uid_store(tag, tail)
        if sub_u == "EXPUNGE":
            return self._uid_expunge(tag, tail)
        self._record(tag, f"UID {sub_u}", (), self._selected, (), "BAD", None)
        return self._tagged(tag, "BAD", f"UID {sub_u} not supported by simulator")

    def _uid_search(self, tag: bytes, criteria: bytes) -> bytes:
        msgs = self._sel_msgs()
        uids = [m.uid for m in msgs]
        joined = " ".join(str(u) for u in uids)
        self._record(tag, "UID SEARCH", (criteria.decode("ascii", "replace"),), self._selected, tuple(uids), "OK", None)
        return self._untagged(f"SEARCH {joined}".rstrip()) + self._tagged(tag, "OK", "UID SEARCH completed")

    def _uid_fetch(self, tag: bytes, tail: bytes) -> bytes:
        uidset, _, items = tail.partition(b" ")
        items_s = items.decode("ascii", "replace")
        want = set(_parse_uidset(uidset))
        msgs = [m for m in self._sel_msgs() if m.uid in want]
        body = self._render_fetch(msgs, items_s)
        self._record(
            tag, "UID FETCH", (uidset.decode("ascii", "replace"), items_s),
            self._selected, tuple(m.uid for m in msgs), "OK", None,
        )
        return body + self._tagged(tag, "OK", "UID FETCH completed")

    def _render_fetch(self, msgs: list[SimMessage], items: str) -> bytes:
        """序列化 FETCH 回應——**只回索取的 data items**（忠實：沒索取 UID 就不回 UID）。

        帶 literal 的回應形如 ``* <seq> FETCH (UID <u> BODY[<sec>] {<n>}\\r\\n<n bytes>)\\r\\n``，
        經真 imaplib 解析為 ``[(b'<seq> (UID <u> BODY[<sec>] {<n>}', b'<literal>'), b')']``。
        """
        uid_m = re.search(r"\bUID\b", items)
        flags_m = re.search(r"\bFLAGS\b", items)
        body_m = re.search(r"BODY(?:\.PEEK)?\[([^\]]*)\]", items)

        order: list[tuple[int, str]] = []
        if uid_m:
            order.append((uid_m.start(), "UID"))
        if flags_m:
            order.append((flags_m.start(), "FLAGS"))
        if body_m:
            order.append((body_m.start(), "BODY"))
        order.sort()
        tokens = [tok for _, tok in order]

        out = b""
        for m in msgs:
            seq = self._seq_of(m)
            prefix: list[str] = []
            suffix: list[str] = []
            literal: Optional[bytes] = None
            hit_body = False
            for tok in tokens:
                if tok == "UID":
                    (suffix if hit_body else prefix).append(f"UID {m.uid}")
                elif tok == "FLAGS":
                    (suffix if hit_body else prefix).append(f"FLAGS ({' '.join(sorted(m.flags))})")
                elif tok == "BODY":
                    section = body_m.group(1)  # type: ignore[union-attr]
                    literal = _render_header_literal(m, section)
                    prefix.append(f"BODY[{section}] {{{len(literal)}}}")
                    hit_body = True
            head = f"* {seq} FETCH (" + " ".join(prefix)
            if literal is not None:
                closer = (" " + " ".join(suffix) if suffix else "") + ")"
                out += head.encode() + CRLF + literal + closer.encode() + CRLF
            else:
                out += (head + (" " + " ".join(suffix) if suffix else "") + ")").encode() + CRLF
        return out

    # ---------- handlers：UID MOVE / COPY / STORE / EXPUNGE（破壞性，鏡像 FakeIMAPConn 語意）----------
    def _uid_move(self, tag: bytes, tail: bytes) -> bytes:
        uid_s, _, dest_b = tail.partition(b" ")
        dest = _unquote(dest_b.decode("utf-8", "replace"))
        uid = int(uid_s)
        if not self._supports_move:
            self._record(tag, "UID MOVE", (uid_s.decode(), dest), self._selected, (), "NO", None)
            return self._tagged(tag, "NO", "MOVE not supported")
        src = self._sel_name()
        if dest not in self.mailboxes:
            self._record(tag, "UID MOVE", (uid_s.decode(), dest), src, (), "NO", "TRYCREATE")
            return self._tagged(tag, "NO", "[TRYCREATE] Mailbox doesn't exist")
        m = self._find(src, uid)
        if m is None:
            self._record(tag, "UID MOVE", (uid_s.decode(), dest), src, (), "NO", None)
            return self._tagged(tag, "NO", "No matching message")
        self.mailboxes[src].remove(m)
        copy = self._append_copy(dest, m)
        self._record(tag, "UID MOVE", (uid_s.decode(), dest), src, (uid,), "OK", "COPYUID")
        return self._tagged(tag, "OK", f"[COPYUID {self._uidvalidity.get(dest, 1)} {uid} {copy.uid}] MOVE completed")

    def _uid_copy(self, tag: bytes, tail: bytes) -> bytes:
        uid_s, _, dest_b = tail.partition(b" ")
        dest = _unquote(dest_b.decode("utf-8", "replace"))
        uid = int(uid_s)
        src = self._sel_name()
        if dest not in self.mailboxes:
            self._record(tag, "UID COPY", (uid_s.decode(), dest), src, (), "NO", "TRYCREATE")
            return self._tagged(tag, "NO", "[TRYCREATE] Mailbox doesn't exist")
        m = self._find(src, uid)
        if m is None:
            self._record(tag, "UID COPY", (uid_s.decode(), dest), src, (), "NO", None)
            return self._tagged(tag, "NO", "No matching message")
        copy = self._append_copy(dest, m)  # 來源保留（複本配發新 UID）
        self._record(tag, "UID COPY", (uid_s.decode(), dest), src, (uid,), "OK", "COPYUID")
        return self._tagged(tag, "OK", f"[COPYUID {self._uidvalidity.get(dest, 1)} {uid} {copy.uid}] COPY completed")

    def _uid_store(self, tag: bytes, tail: bytes) -> bytes:
        uid_s, _, rest2 = tail.partition(b" ")
        op_b, _, flags_b = rest2.partition(b" ")
        op = op_b.decode("ascii", "replace")
        flags_str = flags_b.decode("ascii", "replace")
        m = self._find(self._sel_name(), int(uid_s))
        if m is None:
            self._record(tag, "UID STORE", (uid_s.decode(), op, flags_str), self._selected, (), "NO", None)
            return self._tagged(tag, "NO", "No matching message")
        parsed = {f if f.startswith("\\") else "\\" + f for f in re.findall(r"\\?\w+", flags_str.strip("()"))}
        op_u = op.upper()  # 與 FakeIMAPConn 一致（消除 P1 SR C3 點名的大小寫漂移）
        if op_u.startswith("+"):
            m.flags |= parsed
        elif op_u.startswith("-"):
            m.flags -= parsed
        else:
            m.flags = parsed
        seq = self._seq_of(m)
        self._record(tag, "UID STORE", (uid_s.decode(), op, flags_str), self._selected, (int(uid_s),), "OK", None)
        return self._untagged(f"{seq} FETCH (FLAGS ({' '.join(sorted(m.flags))}))") + self._tagged(
            tag, "OK", "UID STORE completed"
        )

    def _uid_expunge(self, tag: bytes, tail: bytes) -> bytes:
        if not self._supports_uidplus:
            self._record(tag, "UID EXPUNGE", (tail.decode("ascii", "replace"),), self._selected, (), "NO", None)
            return self._tagged(tag, "NO", "UIDPLUS not supported")
        targets = set(_parse_uidset(tail))
        # 只清「指定 UID 且確實標 \Deleted」者——絕不波及他人已標刪郵件（資料安全鐵則）。
        uids = [m.uid for m in self._sel_msgs() if m.uid in targets and DELETED in m.flags]
        lines, removed = self._do_expunge(uids)
        self._record(tag, "UID EXPUNGE", (tail.decode("ascii", "replace"),), self._selected, tuple(removed), "OK", None)
        return lines + self._tagged(tag, "OK", "UID EXPUNGE completed")

    # ---------- 內部：選取狀態 + 資料變動 ----------
    def _sel_name(self) -> str:
        if self._selected is None:
            raise AssertionError("尚未 SELECT 任何信箱（引擎收到需選取的指令）")
        return self._selected

    def _sel_msgs(self) -> list[SimMessage]:
        return self.mailboxes[self._sel_name()]

    def _seq_of(self, m: SimMessage) -> int:
        return self._sel_msgs().index(m) + 1  # IMAP 序號自 1 起

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

    def _do_expunge(self, uids: list[int]) -> tuple[bytes, list[int]]:
        sel = self._sel_name()
        lines = b""
        removed: list[int] = []
        for uid in uids:
            m = self._find(sel, uid)
            if m is not None:
                lines += self._untagged(f"{self._seq_of(m)} EXPUNGE")  # 序號於移除當下計算（後續自動下移）
                self.mailboxes[sel].remove(m)
                removed.append(uid)
        return lines, removed

    # ---------- 序列化助手 ----------
    @staticmethod
    def _tagged(tag: bytes, typ: str, text: str) -> bytes:
        return tag + b" " + typ.encode() + b" " + text.encode() + CRLF

    @staticmethod
    def _untagged(text: str) -> bytes:
        return b"* " + text.encode() + CRLF

    def _record(
        self, tag: bytes, command: str, args: tuple, mailbox: Optional[str],
        affected: tuple, typ: str, code: Optional[str],
    ) -> None:
        self._seq += 1
        self.log.append(
            ServerOp(self._seq, tag.decode("ascii", "replace"), command, tuple(args),
                     mailbox, tuple(affected), typ, code, time.time())
        )

    # ---------- 第二層驗證：資料狀態快照 ----------
    def snapshot(self) -> dict[str, list[tuple[int, frozenset]]]:
        """各信箱狀態深拷貝快照（uid + 旗標集合），供測試前後比對資料變動是否合理。"""
        return {
            name: [(m.uid, frozenset(m.flags)) for m in msgs]
            for name, msgs in self.mailboxes.items()
        }

    # ---------- 分析助手（邏輯驗證 + loop regression / 效能瓶頸）----------
    def commands(self, name: str) -> list[ServerOp]:
        return [op for op in self.log if op.command == name.upper()]

    def command_count(self, name: str) -> int:
        return len(self.commands(name))

    def fetch_count(self, mailbox: Optional[str] = None) -> int:
        """整夾標頭 FETCH 次數（同夾 > 1 即代表冗餘重抓——0.5.x 之前的效能回歸）。"""
        return len(
            [op for op in self.log if op.command == "UID FETCH" and (mailbox is None or op.mailbox == mailbox)]
        )

    def roundtrips(self) -> int:
        """命令往返總數（不含 greeting；含 SASL 續傳的兩段視為一次 AUTHENTICATE）。"""
        return len(self.log)

    def assert_all_fetches_request_uid(self) -> None:
        """釘死 0.5.x 致命回歸類：任何 UID FETCH 都必須索取 UID，否則大聲失敗。"""
        bad = [
            op for op in self.log
            if op.command == "UID FETCH" and "UID" not in (op.args[1] if len(op.args) > 1 else "")
        ]
        assert not bad, f"發現未索取 UID 的 FETCH（會導致 uid 全空）：{bad}"

    def loop_report(self) -> dict:
        """彙整一次操作的 loop-regression / 效能分析數據（大量郵件迴圈回歸的單一檢驗面）。

        關鍵欄位 ``redundant_full_folder_reads`` 非空 = 同一來源夾整夾標頭被重抓（冗餘下載、
        效能回歸——對照 [[no-redundant-refetch]] 鐵則）。其餘供往返/位元組/各命令次數的瓶頸分析。
        """
        counts: dict[str, int] = {}
        fetches: dict[str, int] = {}
        for op in self.log:
            counts[op.command] = counts.get(op.command, 0) + 1
            if op.command == "UID FETCH":
                mb = op.mailbox or "?"
                fetches[mb] = fetches.get(mb, 0) + 1
        destructive = sum(
            counts.get(k, 0) for k in ("UID MOVE", "UID COPY", "UID STORE", "UID EXPUNGE", "EXPUNGE")
        )
        return {
            "roundtrips": len(self.log),
            "bytes_in": sum(len(b) for b in self.wire_in),
            "bytes_out": sum(len(b) for b in self.wire_out),
            "command_counts": counts,
            "fetches_per_folder": fetches,
            "redundant_full_folder_reads": {mb: n for mb, n in fetches.items() if n > 1},
            "authentications": counts.get("AUTHENTICATE", 0),
            "destructive_ops": destructive,
        }

    def dump(self) -> str:
        """除錯用：一次吐 wire transcript + 結構化 log + 快照（失敗時貼上即可定位）。"""
        lines = ["=== wire (C->S / S->C) ==="]
        for b in self.wire_in:
            lines.append(f"C: {b!r}")
        lines.append("--- structured log ---")
        for op in self.log:
            lines.append(repr(op))
        lines.append("--- snapshot ---")
        for name, msgs in self.snapshot().items():
            lines.append(f"{name}: {[(u, sorted(f)) for u, f in msgs]}")
        return "\n".join(lines)
