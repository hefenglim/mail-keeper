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
import email
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

# 沿用既有、已對拍真 imaplib 驗證過的資料模型與編碼助手（單一可信來源）。
from imap_sim import (  # noqa: F401  (DELETED/SEEN/FLAGGED 供注入與測試引用)
    DELETED,
    FLAGGED,
    SEEN,
    SimMessage,
    _decode_mailbox_arg,
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
    """伺服器引擎執行過的一道命令（比舊 ImapCommand 多 tag/影響 UID/結果碼/時間/狀態/延遲）。"""

    seq: int
    tag: str
    command: str                    # 'CAPABILITY'/'AUTHENTICATE'/'SELECT'/'EXAMINE'/'LIST'/'UID SEARCH'/'UID FETCH'/'LOGOUT'…
    args: tuple = ()
    mailbox: Optional[str] = None
    affected_uids: tuple = ()
    result_typ: str = "OK"          # 'OK'/'NO'/'BAD'
    response_code: Optional[str] = None  # 如 'READ-ONLY'/'NONEXISTENT'/'TRYCREATE'…
    t_wall: float = 0.0             # time.time()（真實牆鐘，記錄用）
    t_mono: float = 0.0             # **虛擬**單調時鐘：僅被注入延遲推進（非實測 RTT/throughput），供確定性計時故障測試
    injected_latency_s: float = 0.0  # 本命令被注入的人工延遲（E1，虛擬時鐘，不真睡）
    state_before: str = ""          # 命令前狀態機（NONAUTH/AUTH/SELECTED/LOGOUT）
    state_after: str = ""           # 命令後狀態機

    def __repr__(self) -> str:  # 便於 dump 直接閱讀
        extra = f" mbox={self.mailbox}" if self.mailbox else ""
        uids = f" uids={list(self.affected_uids)}" if self.affected_uids else ""
        code = f" [{self.response_code}]" if self.response_code else ""
        lat = f" +{self.injected_latency_s:g}s" if self.injected_latency_s else ""
        return f"#{self.seq} {self.tag} {self.command} -> {self.result_typ}{code}{extra}{uids}{lat}"


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
    drop_uid : bool
        「不守規矩的伺服器」：即使索取 UID，FETCH 回應也不含 UID（驅動上層防線，釘死 0.5.1 致命 bug）。
    fail_fetch : bool
        批次 UID FETCH 一律回 ``NO``（驅動上層大聲報錯、不靜默回傳不完整標頭）。
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
        enforce_state: bool = True,
        max_connections: Optional[int] = None,
        greeting_mode: str = "ok",
        supports_condstore: bool = False,
        malformed_fold: bool = False,
        send_uidvalidity: bool = True,
    ) -> None:
        self.mailboxes: dict[str, list[SimMessage]] = {
            k: list(v) for k, v in (mailboxes or {"INBOX": []}).items()
        }
        self._sep = sep
        self._supports_move = supports_move
        self._supports_uidplus = supports_uidplus
        self._drop_uid = drop_uid
        self._fail_fetch = fail_fetch
        self._enforce_state = enforce_state          # E8：強制 IMAP 狀態機（非法指令順序回 BAD）
        self._max_connections = max_connections       # E6：連線上限（超限 on_connect 回 * BYE）
        self._greeting_mode = greeting_mode           # P9：'ok'/'preauth'/'no_caps' 招呼變體
        self._supports_condstore = supports_condstore  # P11：CONDSTORE（SELECT 報 HIGHESTMODSEQ、FETCH 可索取 MODSEQ）
        self._highest_modseq = 1                       # P11：CONDSTORE 模擬用
        self._malformed_fold = malformed_fold          # 確定性異常注入：表頭不合規折行（值落續行）
        self._send_uidvalidity = send_uidvalidity      # E3：SELECT/EXAMINE 是否送 [UIDVALIDITY]（False → 罕見伺服器省略）
        self._list_raw: Optional[list[str]] = None     # E2：覆寫 LIST 回應的原始 untagged 內容（畸形 mUTF-7 / 零行）
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
        self._pending_append: Optional[tuple[bytes, str, set]] = None  # P6：(tag, mailbox, flags) 等待 APPEND literal
        self._literal_remaining = 0                              # P6：APPEND 尚待接收的 literal 位元組數

        # 失效注入（P2，傳輸/協定層）：擬真 token 過期→session 作廢→EOF 連環，及 OSError/SSLError/BYE/authfail/timeout。
        self._alive = True
        self._socket_dead: Optional[str] = None  # None/'oserror'/'sslerror'/'timeout'（transport 讀取時據此 raise；'eof' 由空緩衝自然 EOF）
        self._arm: Optional[tuple[str, int, str, bool]] = None  # (op_kind, nth, mode, persist)
        self._op_counts: dict[str, int] = {}

        # 失效注入擴充（E1–E7）：每種注入一個 arm 槽；皆於 feed 的 _special_response / _arm_check 比對 op 次數。
        self._latency_arm: Optional[tuple[str, int, float, bool]] = None   # E1：(op, nth, seconds, persist) 虛擬延遲
        self._resp_arm: Optional[tuple[str, int, str, Optional[str], str, bool]] = None  # E4/E5：(op,nth,typ,code,text,persist)
        self._unsolicited_arm: Optional[tuple[str, int, bytes, bool]] = None  # E5：(op,nth,untagged_line,persist) 非預期/畸形行
        self._truncate_arm: Optional[tuple[str, int, int, bool]] = None     # E3：(op,nth,drop_bytes,persist) 截斷 literal 中途斷
        self._async_arm: Optional[tuple[str, int, tuple[int, ...]]] = None   # E7：(op,nth,uids) in-flight 推 EXPUNGE + 真移除
        self._connect_fail: Optional[tuple[int, str, bool]] = None          # E2：(nth, mode, persist) 連線期失敗
        self._connections = 0
        self._pending_latency = 0.0     # 本命令待計入的注入延遲（_record 讀後清零）

        # E9 進階遙測：虛擬時鐘（被注入延遲推進）、狀態轉移軌跡、故障注入事件。
        self._clock = 0.0                                  # 虛擬單調時鐘（秒）；注入延遲時前進
        self.transitions: list[tuple[str, str, str]] = []  # (from_state, to_state, cause)
        self.fault_events: list[dict] = []                 # 每次注入觸發的標記（type/op/detail/t_mono）

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
        if self._supports_condstore:
            caps.append("CONDSTORE")
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
        caps = " ".join(self._capabilities())
        if self._greeting_mode == "preauth":
            # P9：* PREAUTH → 連線即已認證（imaplib `_connect` 設 state=AUTH，無需 AUTHENTICATE）
            self._authenticated = True
            self._set_state("AUTH", "preauth")
            g = f"* PREAUTH [CAPABILITY {caps}] MailKeeper pre-authenticated".encode() + CRLF
        elif self._greeting_mode == "no_caps":
            # P9：招呼不含 [CAPABILITY] → imaplib 需另送 CAPABILITY 命令探測能力
            self._set_state("NONAUTH", "connect")
            g = b"* OK MailKeeper IMAP simulator ready" + CRLF
        else:
            self._set_state("NONAUTH", "connect")
            g = f"* OK [CAPABILITY {caps}] MailKeeper IMAP simulator ready".encode() + CRLF
        self.wire_out.append(g)
        return g

    def on_connect(self) -> bytes:
        """傳輸層 open() 時呼叫：計數連線、注入連線期失敗（E2）/ 限流（E6），否則回正常 greeting。

        - **連線期失敗**（`arm_connect_failure`）：``timeout`` → 拋 ``socket.timeout``（TCP 逾時）；
          ``tls`` → 拋 ``ssl.SSLError``（握手失敗：憑證/cipher）；``refused`` → 拋 ``ConnectionRefusedError``；
          ``bye`` → 回 ``* BYE``（imaplib `_connect` 視為 error）。皆於連線當下發生（早於 greeting）。
        - **限流**（``max_connections``）：連線數超過上限 → 回 ``* BYE [UNAVAILABLE]``（真 imaplib `_connect`
          因 welcome 非 OK/PREAUTH 而拋 ``IMAP4.error`` → 模擬「達連線上限、拒絕連線」）。
        """
        self._connections += 1
        if self._connect_fail is not None:
            nth, mode, persist = self._connect_fail
            hit = (self._connections >= nth) if persist else (self._connections == nth)
            if hit:
                if not persist:
                    self._connect_fail = None
                self._record_fault("connect", op="CONNECT", detail=mode)
                if mode == "timeout":
                    import socket
                    raise socket.timeout("simulated TCP handshake timeout")
                if mode == "tls":
                    import ssl
                    raise ssl.SSLError("simulated TLS handshake failure (cert/cipher)")
                if mode == "refused":
                    raise ConnectionRefusedError("simulated connection refused")
                if mode == "bye":
                    return self._bye_greeting("Server shutting down")
                raise AssertionError(f"未知連線失敗模式：{mode}")
        if self._max_connections is not None and self._connections > self._max_connections:
            self._record_fault("ratelimit", op="CONNECT", detail=f"max={self._max_connections}")
            return self._bye_greeting("[UNAVAILABLE] Too many simultaneous connections")
        return self.greeting()

    def _bye_greeting(self, text: str) -> bytes:
        g = f"* BYE {text}".encode() + CRLF
        self.wire_out.append(g)
        return g

    def arm_connect_failure(self, *, mode: str = "timeout", nth: int = 1, persist: bool = False) -> None:
        """安排「第 nth 次連線時失敗」（E2/REQ-FAULT-A2/A3）。

        ``mode``：``timeout``（TCP handshake 逾時）/ ``tls``（TLS 握手失敗）/ ``refused``（拒絕連線）/
        ``bye``（伺服器送 BYE）。``persist=True`` 則第 nth 次起每次連線都失敗（→ 重連用盡）。
        """
        self._connect_fail = (nth, mode, persist)

    # ---------- 主入口：收一條命令列，回序列化的 wire bytes ----------
    def feed(self, line: bytes) -> bytes:
        """收 imaplib 送出的「一條命令列」（已去 CRLF），執行並回真實伺服器會送的 wire bytes。"""
        self.wire_in.append(line)
        if line == b"":
            return b""  # 空行（如 APPEND literal 之後的命令終止 CRLF）→ no-op
        if self._pending_sasl is not None:
            resp = self._finish_authenticate(line)
        elif not self._alive:
            resp = b""  # 連線已失效（EOF 連環）：持續回空，直到用戶端重連（greeting 重置）
        else:
            op = self._op_kind(line)
            if op:
                self._op_counts[op] = self._op_counts.get(op, 0) + 1  # 計數集中於此（arm 檢查只讀）
            failure = self._arm_check(op)  # 既有 arm_expiry（傳輸/協定層斷線）
            if failure:
                resp = self._trigger_failure(failure, line)
            else:
                resp = self._special_response(op, line)  # E1/E3/E4/E5/E7 擴充注入，否則正常 dispatch
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
        bad = self._state_error(tag, command)               # E8：非法指令順序 → BAD（不執行）
        if bad is not None:
            return bad
        handler: Optional[Callable[[bytes, bytes], bytes]] = getattr(self, "_cmd_" + command.lower(), None)
        if handler is None:
            self._record(tag, command, (), None, (), "BAD", None)
            return self._tagged(tag, "BAD", f"{command} not supported by simulator")
        return handler(tag, rest)

    # 各命令的最低狀態需求（E8）：'AUTH' = 需已認證（AUTH/SELECTED）；'SELECTED' = 需已選取信箱。
    _STATE_REQUIRES = {
        "LIST": "AUTH", "CREATE": "AUTH", "SELECT": "AUTH", "EXAMINE": "AUTH",
        "LSUB": "AUTH", "NAMESPACE": "AUTH", "STATUS": "AUTH", "APPEND": "AUTH",
        "UID": "SELECTED", "EXPUNGE": "SELECTED",
    }

    def _state_error(self, tag: bytes, command: str) -> Optional[bytes]:
        """E8：強制 IMAP 狀態機——非法指令順序回 ``BAD``（如 AUTH 前 SELECT、未 SELECT 的 UID/EXPUNGE）。

        真 imaplib 客戶端自身也擋這類順序（送出前就拋 error），故本檢查由**引擎自測以原始行直接驅動**
        （繞過客端檢查）來驗證伺服器端防錯；產品正常流程（AUTH→SELECT→UID）不受影響。
        """
        if not self._enforce_state:
            return None
        need = self._STATE_REQUIRES.get(command)
        if need is None:
            return None
        ok = (self._state in ("AUTH", "SELECTED")) if need == "AUTH" else (self._state == "SELECTED")
        if ok:
            return None
        self._record_fault("state_violation", op=command, detail=f"state={self._state} needs={need}")
        self._record(tag, command, (), self._selected, (), "BAD", "CLIENTBUG")
        return self._tagged(tag, "BAD", f"[CLIENTBUG] {command} not allowed in state {self._state}")

    # ---------- 失效注入（P2）：擬真 token 過期 / 連線中斷 / 協定錯誤 ----------
    def arm_expiry(self, *, before_op: str, nth: int = 1, mode: str = "eof", persist: bool = False) -> None:
        """安排「第 nth 次 ``before_op`` 操作時 session 失效」（觸發前不執行該操作 → 不誤動資料）。

        ``mode``（一套注入即覆蓋產品 ``_is_session_lost`` 的全部真實入口）：
          * ``eof``      —— 伺服器靜默關閉連線：feed 回空 bytes → 真 imaplib readline 讀到 EOF →
                            ``abort('socket error: EOF')``（實測 Outlook token 過期的主路徑）。
          * ``oserror`` / ``sslerror`` —— 傳輸層 read 拋 OSError / ssl.SSLError（先前未測分支）。
          * ``timeout``  —— 傳輸層 read 拋 ``socket.timeout``（指令回應逾時；OSError 子類）。
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
        # 計數已於 feed 集中遞增；此處只讀比對。
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

    def _hit(self, op: str, arm_op: str, arm_nth: int, persist: bool) -> bool:
        """擴充 arm 的共用命中判定（依 op 累計次數，one-shot 用 == 、persist 用 >=）。"""
        if not op or op != arm_op:
            return False
        c = self._op_counts.get(op, 0)
        return (c >= arm_nth) if persist else (c == arm_nth)

    def _full_command(self, line: bytes) -> str:
        """命令標籤（UID 子命令展開成 'UID MOVE' 等），供注入回應的 log 記錄用。"""
        parts = line.split(b" ", 2)
        if len(parts) < 2:
            return "?"
        cmd = parts[1].upper().decode("ascii", "replace")
        if cmd == "UID" and len(parts) > 2:
            return "UID " + parts[2].split(b" ", 1)[0].upper().decode("ascii", "replace")
        return cmd

    def _record_fault(self, kind: str, *, op: str = "", detail: str = "") -> None:
        """E9：故障注入事件標記（注入類型 / 觸發時機 op / 細節 / 虛擬時鐘）。"""
        self.fault_events.append(
            {"kind": kind, "op": op, "detail": detail, "t_mono": self._clock, "after_seq": self._seq}
        )

    # ---------- 擴充注入：延遲 / 回應替換 / 非預期行 / 截斷 / 非同步推送 ----------
    def _special_response(self, op: str, line: bytes) -> bytes:
        self._apply_latency(op)                              # E1：虛擬延遲（不真睡）
        replaced = self._resp_replacement(op, line)          # E4/E5：直接回 NO/BAD [code]
        if replaced is not None:
            return replaced
        prefix = self._unsolicited_prefix(op, line)          # E5：非預期行；E7：非同步 EXPUNGE（真移除）
        body = prefix + self._dispatch(line)
        return self._maybe_truncate(op, body)                # E3：截斷 literal 中途斷

    def _apply_latency(self, op: str) -> None:
        if self._latency_arm is None:
            return
        a_op, a_nth, secs, persist = self._latency_arm
        if self._hit(op, a_op, a_nth, persist):
            self._pending_latency = secs  # _record 讀後推進虛擬時鐘
            self._record_fault("latency", op=op, detail=f"{secs}s")
            if not persist:
                self._latency_arm = None

    def _resp_replacement(self, op: str, line: bytes) -> Optional[bytes]:
        if self._resp_arm is None:
            return None
        a_op, a_nth, typ, code, text, persist = self._resp_arm
        if not self._hit(op, a_op, a_nth, persist):
            return None
        if not persist:
            self._resp_arm = None
        tag = line.split(b" ", 1)[0]
        label = self._full_command(line)
        body = (f"[{code}] " if code else "") + (text or f"{label} rejected (simulated)")
        self._record_fault("response", op=op, detail=f"{typ} {code or ''}".strip())
        self._record(tag, label, (), self._selected, (), typ, code)
        return self._tagged(tag, typ, body)

    def _unsolicited_prefix(self, op: str, line: bytes) -> bytes:
        prefix = b""
        if self._unsolicited_arm is not None:
            a_op, a_nth, uline, persist = self._unsolicited_arm
            if self._hit(op, a_op, a_nth, persist):
                prefix += uline + CRLF
                self._record_fault("unsolicited", op=op, detail=uline.decode("ascii", "replace")[:48])
                if not persist:
                    self._unsolicited_arm = None
        if self._async_arm is not None:
            a_op, a_nth, uids = self._async_arm
            if self._hit(op, a_op, a_nth, False) and self._selected is not None:
                # 多封：逐封移除並於**移除當下**計算序號 → 後續序號自動下移（RFC 3501 EXPUNGE 重編序號）。
                for uid in uids:
                    m = self._find(self._selected, uid)
                    if m is not None:
                        seq = self._seq_of(m)
                        self.mailboxes[self._selected].remove(m)  # 他處刪除 → 真實移除（非同步狀態變更）
                        prefix += self._untagged(f"{seq} EXPUNGE")
                        self._record_fault("async_expunge", op=op, detail=f"uid={uid} seq={seq}")
                self._async_arm = None
        return prefix

    def _maybe_truncate(self, op: str, body: bytes) -> bytes:
        if self._truncate_arm is None:
            return body
        a_op, a_nth, drop, persist = self._truncate_arm
        if not self._hit(op, a_op, a_nth, persist):
            return body
        if not persist:
            self._truncate_arm = None
        self._record_fault("truncate", op=op, detail=f"drop={drop}")
        return body[:-drop] if 0 < drop < len(body) else b""  # 回應中途截斷 → imaplib 讀不到 tagged → 受控 EOF/abort

    # ---------- 擴充注入的 arm API ----------
    def arm_latency(self, before_op: str, seconds: float, *, nth: int = 1, persist: bool = False) -> None:
        """E1：第 nth 次 ``before_op`` 注入虛擬延遲（秒，推進虛擬時鐘、不真睡）。供 RTT/瓶頸分析。"""
        self._latency_arm = (before_op.upper(), nth, float(seconds), persist)

    def arm_response(
        self, before_op: str, *, typ: str = "NO", code: Optional[str] = None,
        text: str = "", nth: int = 1, persist: bool = False,
    ) -> None:
        """E4/E5：第 nth 次 ``before_op`` 直接回 ``typ [code] text``（op 不執行）。

        例：``arm_response('SELECT', code='UNAVAILABLE')``、``arm_response('MOVE', code='OVERQUOTA')``、
        ``arm_response('FETCH', typ='BAD', text='syntax error')``。
        """
        self._resp_arm = (before_op.upper(), nth, typ.upper(), code, text, persist)

    def arm_unsolicited(
        self, before_op: str, *, line: bytes = b"* OK [ALERT] Server maintenance soon",
        nth: int = 1, persist: bool = False,
    ) -> None:
        """E5：第 nth 次 ``before_op`` 回應**前**夾帶一條非預期/畸形 untagged 行（測產品 parser 容錯）。"""
        self._unsolicited_arm = (before_op.upper(), nth, line, persist)

    def arm_truncate(self, before_op: str = "FETCH", *, nth: int = 1, drop: int = 8, persist: bool = False) -> None:
        """E3：第 nth 次 ``before_op`` 回應自尾端截掉 ``drop`` bytes（含 tagged 收尾）→ 模擬資料傳輸中途斷。"""
        self._truncate_arm = (before_op.upper(), nth, drop, persist)

    def arm_async_expunge(self, uids: "int | list[int]", *, before_op: str, nth: int = 1) -> None:
        """E7：第 nth 次 ``before_op`` 進行中，伺服器主動推 ``* <seq> EXPUNGE``（他處刪除）並真實移除。

        ``uids`` 可為單一 UID 或多個（依序移除、序號逐封重編——RFC 3501 EXPUNGE 語意）。
        """
        uid_list = [uids] if isinstance(uids, int) else list(uids)
        self._async_arm = (before_op.upper(), nth, tuple(uid_list))

    def arm_exists(self, count: int, *, before_op: str, nth: int = 1, persist: bool = False) -> None:
        """P2：第 nth 次 ``before_op`` 回應前推 ``* <count> EXISTS``（信箱成長的非請求通知）。

        與 ``arm_unsolicited`` 共用 ``_unsolicited_arm`` 槽 → 兩者每 session 互斥（後設者覆蓋前者）。
        """
        self._unsolicited_arm = (before_op.upper(), nth, f"* {count} EXISTS".encode(), persist)

    def set_uidvalidity(self, mailbox: str, value: int, *, reassign_uids: bool = False) -> None:
        """P3：session 中途變更信箱 UIDVALIDITY（下次 SELECT/EXAMINE 即報告新值）。

        ``reassign_uids=True`` 進一步模擬「信箱重建、舊 UID 全失效」——重新配發 UID（舊 UID 不再有效，
        客戶端若用過時 UID 操作將指向錯誤/不存在郵件，正是 UIDVALIDITY 變更要防的最危險 bug 類）。
        """
        self._uidvalidity[mailbox] = value
        if reassign_uids and mailbox in self.mailboxes:
            base = max(self._uidnext.get(mailbox, 1), 9000)
            for i, m in enumerate(self.mailboxes[mailbox]):
                m.uid = base + i
            self._uidnext[mailbox] = base + len(self.mailboxes[mailbox])

    def _trigger_failure(self, mode: str, line: bytes) -> bytes:
        tag = line.split(b" ", 1)[0]
        self._record_fault("session_loss", op=self._op_kind(line), detail=mode)
        if mode in ("eof", "oserror", "sslerror", "timeout"):
            self._alive = False
            self._socket_dead = None if mode == "eof" else mode
            return b""  # eof：空 inbuf → imaplib EOF abort；oserror/sslerror/timeout：transport read 時拋
        if mode == "bye":
            self._alive = False
            return self._untagged("BYE session terminated - AccessTokenExpired")
        if mode == "authfail":
            return self._tagged(tag, "BAD", "[AUTHENTICATIONFAILED] Session invalidated - AccessTokenExpired")
        raise AssertionError(f"未知失效注入模式：{mode}")

    def raise_if_socket_dead(self) -> None:
        """供傳輸層 read/readline 呼叫：oserror/sslerror/timeout 模式時拋對應例外（eof 由空緩衝自然 EOF）。"""
        if self._socket_dead == "oserror":
            raise OSError("simulated connection reset by peer")
        if self._socket_dead == "sslerror":
            import ssl

            raise ssl.SSLError("simulated SSL error during session")
        if self._socket_dead == "timeout":
            import socket

            raise socket.timeout("simulated read timeout")  # OSError 子類 → 產品 _is_session_lost 視為斷線

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
        self._set_state("AUTH", "AUTHENTICATE")
        self._record(tag, "AUTHENTICATE", (mech,), None, (), "OK", None)
        return self._tagged(tag, "OK", "AUTHENTICATE completed")

    def _cmd_logout(self, tag: bytes, rest: bytes) -> bytes:
        self._set_state("LOGOUT", "LOGOUT")
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
        raw = rest.decode("utf-8", "replace")
        name = _decode_mailbox_arg(raw)
        if name is None:  # 未加引號含空白等語法錯誤 → BAD（保真：真伺服器拒絕，F3）
            self._record(tag, verb, (raw,), None, (), "BAD", None)
            return self._tagged(tag, "BAD", f"{verb} mailbox name must be a quoted string or atom")
        if name not in self.mailboxes:
            self._set_state("AUTH", f"{verb} NONEXISTENT")
            self._selected = None
            self._record(tag, verb, (name,), name, (), "NO", "NONEXISTENT")
            return self._tagged(tag, "NO", f"[NONEXISTENT] Mailbox doesn't exist: {name}")
        self._selected = name
        self._readonly = readonly
        self._set_state("SELECTED", verb)
        msgs = self.mailboxes[name]
        flags = r"\Answered \Flagged \Deleted \Seen \Draft"
        code = "READ-ONLY" if readonly else "READ-WRITE"
        body = (
            self._untagged(f"FLAGS ({flags})")
            + self._untagged(f"{len(msgs)} EXISTS")
            + self._untagged("0 RECENT")
        )
        if self._send_uidvalidity:  # E3：False → 省略 [UIDVALIDITY]（罕見伺服器；驅動產品 _current_uidvalidity 回 None）
            body += self._untagged(f"OK [UIDVALIDITY {self._uidvalidity.get(name, 1)}] UIDs valid")
        body += self._untagged(f"OK [UIDNEXT {self._uidnext.get(name, 1)}] Predicted next UID")
        if self._supports_condstore:  # P11：CONDSTORE → 報告 HIGHESTMODSEQ
            body += self._untagged(f"OK [HIGHESTMODSEQ {self._highest_modseq}] Highest")
        body += self._tagged(tag, "OK", f"[{code}] {verb} completed")
        self._record(tag, verb, (name,), name, (), "OK", code)
        return body

    # ---------- handlers：CREATE（確保資料夾）----------
    def _cmd_create(self, tag: bytes, rest: bytes) -> bytes:
        raw = rest.decode("utf-8", "replace")
        name = _decode_mailbox_arg(raw)
        if name is None:  # F3：未加引號含空白等 → BAD
            self._record(tag, "CREATE", (raw,), None, (), "BAD", None)
            return self._tagged(tag, "BAD", "CREATE mailbox name must be a quoted string or atom")
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
        if self._list_raw is not None:
            # E2：覆寫原始 untagged 內容——測畸形 mUTF-7 夾名與「零 LIST 行」（imaplib data=[None]）等罕見回應。
            lines = b"".join(self._untagged(s) for s in self._list_raw)
            names: tuple = tuple(self._list_raw)
        else:
            lines = b"".join(
                self._untagged(f'LIST (\\HasNoChildren) "{self._sep}" "{_encode_mutf7(name)}"')
                for name in self.mailboxes
            )
            names = tuple(self.mailboxes)
        self._record(tag, "LIST", ('""', "*"), None, names, "OK", None)
        return lines + self._tagged(tag, "OK", "LIST completed")

    def set_list_lines(self, lines: "Optional[list[str]]") -> None:
        """E2：覆寫下一次（起）``LIST`` 回應的原始 untagged payload（每元素為一條，不含 ``* ``/CRLF）。

        ``[]`` → 不送任何 ``* LIST`` 行（真 imaplib `list()` 回 ``data=[None]``，驅動產品略過空行）；
        ``None`` → 還原為依 ``self.mailboxes`` 正常列舉。供測試畸形 mUTF-7 夾名與零行回應。
        """
        self._list_raw = list(lines) if lines is not None else None

    # ---------- handlers：LSUB / NAMESPACE / STATUS（P11）----------
    def _cmd_lsub(self, tag: bytes, rest: bytes) -> bytes:
        lines = b"".join(
            self._untagged(f'LSUB (\\HasNoChildren) "{self._sep}" "{_encode_mutf7(name)}"')
            for name in self.mailboxes
        )
        self._record(tag, "LSUB", ('""', "*"), None, tuple(self.mailboxes), "OK", None)
        return lines + self._tagged(tag, "OK", "LSUB completed")

    def _cmd_namespace(self, tag: bytes, rest: bytes) -> bytes:
        self._record(tag, "NAMESPACE", (), None, (), "OK", None)
        return self._untagged(f'NAMESPACE (("" "{self._sep}")) NIL NIL') + self._tagged(
            tag, "OK", "NAMESPACE completed"
        )

    def _cmd_status(self, tag: bytes, rest: bytes) -> bytes:
        s = rest.decode("utf-8", "replace")
        mbox_part, _, items_part = s.partition("(")
        name = _unquote(mbox_part.strip())
        items = items_part.rstrip(") ").split()
        if name not in self.mailboxes:
            self._record(tag, "STATUS", (name,), name, (), "NO", "NONEXISTENT")
            return self._tagged(tag, "NO", f"[NONEXISTENT] Mailbox doesn't exist: {name}")
        msgs = self.mailboxes[name]
        values = {
            "MESSAGES": len(msgs),
            "RECENT": 0,
            "UIDNEXT": self._uidnext.get(name, 1),
            "UIDVALIDITY": self._uidvalidity.get(name, 1),
            "UNSEEN": sum(1 for m in msgs if SEEN not in m.flags),
            "HIGHESTMODSEQ": self._highest_modseq,
        }
        rendered = " ".join(f"{it} {values.get(it.upper(), 0)}" for it in items)
        self._record(tag, "STATUS", (name,), name, (), "OK", None)
        return self._untagged(f'STATUS "{name}" ({rendered})') + self._tagged(tag, "OK", "STATUS completed")

    # ---------- handlers：APPEND（同步 literal 續傳，P6）----------
    def _cmd_append(self, tag: bytes, rest: bytes) -> bytes:
        """APPEND mailbox [(flags)] ["date"] {N[+]}：回 ``+`` 續傳並掛起，literal 由 ``feed_literal`` 收。

        支援同步 literal（``{N}`` → 回 ``+`` 等待）與非同步 ``LITERAL+``（``{N+}`` → 不回續傳）。
        """
        s = rest.decode("utf-8", "replace")
        mlit = re.search(r"\{(\d+)(\+?)\}\s*$", s)
        if mlit is None:
            self._record(tag, "APPEND", (), self._selected, (), "BAD", None)
            return self._tagged(tag, "BAD", "APPEND expects a literal")
        size = int(mlit.group(1))
        nonsync = mlit.group(2) == "+"
        head = s[: mlit.start()].strip()
        name = _unquote(head.split(" ", 1)[0])
        fm = re.search(r"\(([^)]*)\)", head)
        flags = {f if f.startswith("\\") else "\\" + f for f in (fm.group(1).split() if fm else [])}
        self._pending_append = (tag, name, flags)
        self._literal_remaining = size
        return b"" if nonsync else b"+ Ready for literal data" + CRLF

    def expecting_literal(self) -> int:
        """供傳輸層查詢：尚待接收的 literal 位元組數（>0 時傳輸層改以原始位元組餵 ``feed_literal``）。"""
        return self._literal_remaining

    def feed_literal(self, data: bytes) -> bytes:
        """收 APPEND 的 literal 原始位元組（可含 CRLF），完成附加並回 tagged 回應。"""
        self.wire_in.append(data)
        assert self._pending_append is not None
        tag, name, flags = self._pending_append
        self._pending_append = None
        self._literal_remaining = 0
        if name not in self.mailboxes:
            self._record(tag, "APPEND", (name,), name, (), "NO", "TRYCREATE")
            resp = self._tagged(tag, "NO", "[TRYCREATE] Mailbox doesn't exist")
            self.wire_out.append(resp)
            return resp
        new_uid = self._uidnext.get(name, 1)
        self._uidnext[name] = new_uid + 1
        self._highest_modseq += 1
        parsed = email.message_from_bytes(data)
        fields = {k.upper(): (parsed.get(k) or "") for k in ("Subject", "From", "To", "Date")}
        self.mailboxes[name].append(SimMessage(new_uid, fields, set(flags), data))
        self._record(tag, "APPEND", (name,), name, (new_uid,), "OK", "APPENDUID")
        resp = self._tagged(
            tag, "OK", f"[APPENDUID {self._uidvalidity.get(name, 1)} {new_uid}] APPEND completed"
        )
        self.wire_out.append(resp)
        return resp

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
        s = criteria.decode("utf-8", "replace").strip()
        toks = re.findall(r'"[^"]*"|\S+', s)  # 尊重引號字串
        matched = [m for m in self._sel_msgs() if self._search_match(m, toks)]
        uids = [m.uid for m in matched]
        joined = " ".join(str(u) for u in uids)
        self._record(tag, "UID SEARCH", (s,), self._selected, tuple(uids), "OK", None)
        return self._untagged(f"SEARCH {joined}".rstrip()) + self._tagged(tag, "OK", "UID SEARCH completed")

    def _search_match(self, m: SimMessage, toks: list[str]) -> bool:
        """SEARCH 條件子集（P5，皆 AND）：ALL / SEEN·UNSEEN / DELETED·UNDELETED / FLAGGED·UNFLAGGED /
        FROM·TO·SUBJECT &lt;substr&gt; / HEADER &lt;field&gt; &lt;substr&gt; / UID &lt;set&gt; / BODY &lt;substr&gt;。未知 token 從寬略過。

        前瞻基建：產品目前只送 ``SEARCH ALL``；FROM/SUBJECT/BODY 等供未來伺服器端過濾的開發即用。
        ``BODY`` 只比對**內文**（表頭後），與 RFC 3501 一致（header-only 訊息其內文為空）。
        """
        i = 0
        while i < len(toks):
            t = toks[i].upper()
            if t in ("ALL", "RECENT", "NEW", "OLD"):
                i += 1
            elif t == "CHARSET":
                i += 2  # 略過 "CHARSET utf-8"
            elif t == "UNSEEN":
                if SEEN in m.flags:
                    return False
                i += 1
            elif t == "SEEN":
                if SEEN not in m.flags:
                    return False
                i += 1
            elif t == "DELETED":
                if DELETED not in m.flags:
                    return False
                i += 1
            elif t == "UNDELETED":
                if DELETED in m.flags:
                    return False
                i += 1
            elif t == "FLAGGED":
                if FLAGGED not in m.flags:
                    return False
                i += 1
            elif t == "UNFLAGGED":
                if FLAGGED in m.flags:
                    return False
                i += 1
            elif t in ("FROM", "TO", "SUBJECT") and i + 1 < len(toks):
                if toks[i + 1].strip('"').lower() not in m.fields.get(t, "").lower():
                    return False
                i += 2
            elif t == "HEADER" and i + 2 < len(toks):
                if toks[i + 2].strip('"').lower() not in m.fields.get(toks[i + 1].strip('"').upper(), "").lower():
                    return False
                i += 3
            elif t == "BODY" and i + 1 < len(toks):
                raw = self._raw_of(m)
                body = raw.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in raw else b""  # 只比內文（RFC 3501）
                if toks[i + 1].strip('"').encode("utf-8", "replace") not in body:
                    return False
                i += 2
            elif t == "UID" and i + 1 < len(toks):
                if m.uid not in set(_parse_uidset(toks[i + 1])):
                    return False
                i += 2
            else:
                i += 1  # 未知/不支援 → 從寬
        return True

    def _uid_fetch(self, tag: bytes, tail: bytes) -> bytes:
        uidset, _, items = tail.partition(b" ")
        items_s = items.decode("ascii", "replace")
        if self._fail_fetch:  # 不守規矩：批次 FETCH 一律失敗 → 上層應大聲報錯
            self._record(tag, "UID FETCH", (uidset.decode("ascii", "replace"), items_s), self._selected, (), "NO", None)
            return self._tagged(tag, "NO", "FETCH failed (simulated)")
        want = set(_parse_uidset(uidset))
        msgs = [m for m in self._sel_msgs() if m.uid in want]
        # 擬真：非 PEEK 的 BODY[...] / 整封 RFC822 會把抓到的郵件設 \Seen（真實 IMAP 副作用）；產品一律
        # 用 .PEEK，故產品路徑不受影響——此分支僅在直接驅動引擎送非 PEEK 請求時生效。
        if not self._readonly and (re.search(r"\bBODY\[", items_s) or re.search(r"\bRFC822\b(?!\.)", items_s)):
            for m in msgs:
                m.flags.add(SEEN)
        body = self._render_fetch(msgs, items_s)
        self._record(
            tag, "UID FETCH", (uidset.decode("ascii", "replace"), items_s),
            self._selected, tuple(m.uid for m in msgs), "OK", None,
        )
        return body + self._tagged(tag, "OK", "UID FETCH completed")

    def _render_fetch(self, msgs: list[SimMessage], items: str) -> bytes:
        """序列化 FETCH 回應——**只回索取的 data items**（忠實：沒索取 UID 就不回 UID）。

        支援 inline 項（UID / FLAGS / RFC822.SIZE / BODYSTRUCTURE）與**單一** literal 項
        （``BODY[<sec>]`` / ``BODY.PEEK[<sec>]`` / 整封 ``RFC822``）。帶 literal 的回應形如
        ``* <seq> FETCH (UID <u> BODY[<sec>] {<n>}\\r\\n<n bytes> ...)\\r\\n``，經真 imaplib 解析為
        ``[(b'<seq> (... {<n>}', b'<literal>'), b' ...)']``。產品只用 ``BODY.PEEK[HEADER.FIELDS(...)]``；
        其餘（內文/結構，E11）供未來抓內文/附件的開發直接以引擎實測。
        """
        # 各 inline 項：(位置, 渲染函式 msg->str)；literal 至多一項：(位置, 標籤, bytes 渲染函式)。
        inline: list[tuple[int, Callable[[SimMessage], str]]] = []
        if not self._drop_uid:
            mm = re.search(r"\bUID\b", items)  # drop_uid：索取了也不回 UID
            if mm:
                inline.append((mm.start(), lambda m: f"UID {m.uid}"))
        fm = re.search(r"\bFLAGS\b", items)
        if fm:
            inline.append((fm.start(), lambda m: f"FLAGS ({' '.join(sorted(m.flags))})"))
        szm = re.search(r"\bRFC822\.SIZE\b", items)
        if szm:
            inline.append((szm.start(), lambda m: f"RFC822.SIZE {len(self._raw_of(m))}"))
        bsm = re.search(r"\bBODYSTRUCTURE\b", items)
        if bsm:
            inline.append((bsm.start(), lambda m: f"BODYSTRUCTURE {self._bodystructure(m)}"))
        msm = re.search(r"\bMODSEQ\b", items)  # P11：CONDSTORE FETCH MODSEQ
        if msm:
            inline.append((msm.start(), lambda m: f"MODSEQ ({self._highest_modseq})"))

        literal_spec: Optional[tuple[int, str, Callable[[SimMessage], bytes]]] = None
        # P7：BODY[sec]<offset.length> 部分取回 → 回應標籤帶 <offset>、literal 取對應切片
        body_m = re.search(r"BODY(?:\.PEEK)?\[([^\]]*)\](?:<(\d+)\.(\d+)>)?", items)
        rfc_m = re.search(r"\bRFC822\b(?!\.)", items)  # 整封 RFC822（非 .SIZE/.HEADER/.TEXT）
        if body_m:
            section = body_m.group(1)
            base = self._body_section(section)
            if body_m.group(2) is not None:
                off, length = int(body_m.group(2)), int(body_m.group(3))

                def render_partial(m: SimMessage, r: Callable[[SimMessage], bytes] = base,
                                   o: int = off, n: int = length) -> bytes:
                    return r(m)[o:o + n]
                literal_spec = (body_m.start(), f"BODY[{section}]<{off}>", render_partial)
            else:
                literal_spec = (body_m.start(), f"BODY[{section}]", base)
        elif rfc_m:
            literal_spec = (rfc_m.start(), "RFC822", self._raw_of)

        out = b""
        for m in msgs:
            seq = self._seq_of(m)
            before: list[str] = []
            after: list[str] = []
            lit_pos = literal_spec[0] if literal_spec else None
            for pos, render in sorted(inline):
                (after if (lit_pos is not None and pos > lit_pos) else before).append(render(m))
            head = f"* {seq} FETCH (" + " ".join(before)
            if literal_spec is not None:
                _, label, render_b = literal_spec
                lit = render_b(m)
                head += (" " if before else "") + f"{label} {{{len(lit)}}}"
                closer = (" " + " ".join(after) if after else "") + ")"
                out += head.encode() + CRLF + lit + closer.encode() + CRLF
            else:
                out += (head + ")").encode() + CRLF
        return out

    # ---------- E11：MIME 內文 / 結構序列化助手 ----------
    def _raw_of(self, m: SimMessage) -> bytes:
        """整封 RFC822 bytes：MIME 訊息用其 ``raw``；header-only 訊息退回「全表頭 + 空行」。"""
        raw = getattr(m, "raw", None)
        if raw is not None:
            return raw
        return _render_header_literal(m, "HEADER", malformed_fold=self._malformed_fold)  # 無 HEADER.FIELDS(...) → 全欄位

    def _body_section(self, section: str) -> Callable[[SimMessage], bytes]:
        """回傳 ``BODY[<section>]`` 的 literal 渲染函式。"""
        sec = section.strip().upper()
        if sec == "":
            return self._raw_of                                   # BODY[]：整封

        if sec == "TEXT":
            def text_body(m: SimMessage) -> bytes:                # BODY[TEXT]：表頭後的內文
                raw = self._raw_of(m)
                return raw.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in raw else b""
            return text_body

        def header_section(m: SimMessage) -> bytes:               # HEADER / HEADER.FIELDS(...)
            return _render_header_literal(m, section, malformed_fold=self._malformed_fold)
        return header_section

    def _bodystructure(self, m: SimMessage) -> str:
        """以括號結構描述郵件 MIME 結構（真 imaplib 僅原樣擷取 bytes，故只需括號平衡且擬真）。"""
        return self._struct(email.message_from_bytes(self._raw_of(m)))

    def _struct(self, part: Any) -> str:
        if part.is_multipart():
            subs = "".join(self._struct(p) for p in part.get_payload())
            return f'({subs} "{part.get_content_subtype().upper()}")'
        csub = part.get_content_subtype().upper()
        ctype = part.get_content_maintype().upper()
        enc = (part.get("Content-Transfer-Encoding") or "7BIT").upper()
        # RFC 3501：body-fld-octets / body-fld-lines 皆為「**編碼後（on-wire）**」之計數，非解碼後
        # （例 base64 附件回報其 base64 文字的位元組數，而非原始 bytes 數）。
        encoded = part.get_payload(decode=False)
        enc_bytes = encoded.encode("ascii", "surrogateescape") if isinstance(encoded, str) else (encoded or b"")
        octets = len(enc_bytes)
        if ctype == "TEXT":
            charset = (part.get_content_charset() or "US-ASCII").upper()
            lines = enc_bytes.count(b"\n")
            return f'("TEXT" "{csub}" ("CHARSET" "{charset}") NIL NIL "{enc}" {octets} {lines})'
        name = part.get_filename()
        params = f'("NAME" "{name}")' if name else "NIL"
        return f'("{ctype}" "{csub}" {params} NIL NIL "{enc}" {octets})'

    # ---------- handlers：UID MOVE / COPY / STORE / EXPUNGE（破壞性，鏡像 FakeIMAPConn 語意）----------
    def _uid_move(self, tag: bytes, tail: bytes) -> bytes:
        # 支援單一 UID 或 UID 集合（批次：'101,102,103' / '101:108'）——真實 UID MOVE 接受 sequence set。
        uid_s, _, dest_b = tail.partition(b" ")
        label = uid_s.decode("ascii", "replace")
        dest = _decode_mailbox_arg(dest_b.decode("utf-8", "replace"))
        if dest is None:  # F3：目標夾名未加引號含空白等 → BAD
            self._record(tag, "UID MOVE", (label, dest_b.decode("utf-8", "replace")), self._selected, (), "BAD", None)
            return self._tagged(tag, "BAD", "UID MOVE mailbox name must be a quoted string or atom")
        uids = _parse_uidset(uid_s)
        if not self._supports_move:
            self._record(tag, "UID MOVE", (label, dest), self._selected, (), "NO", None)
            return self._tagged(tag, "NO", "MOVE not supported")
        src = self._sel_name()
        if dest not in self.mailboxes:
            self._record(tag, "UID MOVE", (label, dest), src, (), "NO", "TRYCREATE")
            return self._tagged(tag, "NO", "[TRYCREATE] Mailbox doesn't exist")
        moved: list[int] = []
        dest_uids: list[int] = []
        for uid in uids:
            m = self._find(src, uid)
            if m is None:
                continue  # 集合中不存在者略過（真實伺服器搬其餘、整體仍 OK）
            self.mailboxes[src].remove(m)
            copy = self._append_copy(dest, m)
            moved.append(uid)
            dest_uids.append(copy.uid)
        if not moved:
            self._record(tag, "UID MOVE", (label, dest), src, (), "NO", None)
            return self._tagged(tag, "NO", "No matching message")
        self._record(tag, "UID MOVE", (label, dest), src, tuple(moved), "OK", "COPYUID")
        srcset = ",".join(str(u) for u in moved)
        dstset = ",".join(str(u) for u in dest_uids)
        return self._tagged(
            tag, "OK", f"[COPYUID {self._uidvalidity.get(dest, 1)} {srcset} {dstset}] MOVE completed"
        )

    def _uid_copy(self, tag: bytes, tail: bytes) -> bytes:
        uid_s, _, dest_b = tail.partition(b" ")
        dest = _decode_mailbox_arg(dest_b.decode("utf-8", "replace"))
        if dest is None:  # F3：目標夾名未加引號含空白等 → BAD
            self._record(tag, "UID COPY", (uid_s.decode("ascii", "replace"), dest_b.decode("utf-8", "replace")), self._selected, (), "BAD", None)
            return self._tagged(tag, "BAD", "UID COPY mailbox name must be a quoted string or atom")
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

    def _set_state(self, new: str, cause: str) -> None:
        """狀態機轉移（E9）：記錄 (from, to, cause) 軌跡，供驗證產品遵循合法 IMAP 狀態機操作。"""
        if new != self._state:
            self.transitions.append((self._state, new, cause))
        self._state = new

    def _record(
        self, tag: bytes, command: str, args: tuple, mailbox: Optional[str],
        affected: tuple, typ: str, code: Optional[str],
    ) -> None:
        self._seq += 1
        lat = self._pending_latency
        self._pending_latency = 0.0
        self._clock += lat                                  # 虛擬時鐘被注入延遲推進（E1）
        state_before = self.log[-1].state_after if self.log else "NONAUTH"
        self.log.append(
            ServerOp(self._seq, tag.decode("ascii", "replace"), command, tuple(args),
                     mailbox, tuple(affected), typ, code, time.time(),
                     t_mono=self._clock, injected_latency_s=lat,
                     state_before=state_before, state_after=self._state)
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
            "redundant_selects": self.redundant_selects(),
            "authentications": counts.get("AUTHENTICATE", 0),
            "reconnects": max(0, counts.get("AUTHENTICATE", 0) - 1),  # 首次 + 每次重連各一認證
            "destructive_ops": destructive,
            "fault_events": list(self.fault_events),                 # E9：注入了哪些故障（type/op/時機）
            "state_transitions": list(self.transitions),             # E9：狀態機軌跡
            "injected_latency_total_s": sum(op.injected_latency_s for op in self.log),  # E1：累計注入延遲
            "connections": self._connections,                        # E6：連線次數（churning 偵測）
        }

    def redundant_selects(self) -> int:
        """連續對「同一個已選取信箱」重複 SELECT/EXAMINE 的次數（可省的往返——效能浪費）。

        例：分類迴圈中產品對每封 move 前都重 SELECT 來源夾，雖正確但每次都選同一已選夾 →
        本計數即點出這類可優化的重複（每來源夾批次只需 SELECT 一次）。
        """
        cur: Optional[tuple] = None  # (mailbox, response_code) —— 兼顧讀寫模式：模式切換的重選不算浪費
        waste = 0
        for op in self.log:
            if op.command in ("SELECT", "EXAMINE"):
                key = (op.mailbox, op.response_code)
                if key == cur:
                    waste += 1
                cur = key
        return waste

    def bottleneck(self) -> dict:
        """指出最可能的效能瓶頸：最高次數命令 + 可省的重複 SELECT / 整夾重抓（供 loop regression 分析）。"""
        counts: dict[str, int] = {}
        for op in self.log:
            counts[op.command] = counts.get(op.command, 0) + 1
        top = max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0)
        return {
            "most_frequent_command": top[0],
            "most_frequent_count": top[1],
            "redundant_selects": self.redundant_selects(),
            "redundant_full_folder_reads": {
                mb: n
                for mb, n in (
                    (op.mailbox, sum(1 for o in self.log if o.command == "UID FETCH" and o.mailbox == op.mailbox))
                    for op in self.log
                    if op.command == "UID FETCH"
                )
                if n > 1
            },
        }

    def assert_sequence(self, expected: list, *, subsequence: bool = True) -> None:
        """E10 序列對齊器：斷言「期望行為序列」對齊實際命令 log。

        ``expected`` 每項可為：``"UID MOVE"``（只比命令）、``("UID FETCH", "OK")``（命令+結果碼）、
        ``("SELECT", "NO", "NONEXISTENT")``（再加 response code）、或 ``{"command": ..., "mailbox": ...}``。
        ``subsequence=True``（預設）：期望項須**依序**出現（允許其間有其他命令），用以驗證如
        「注入認證失敗 → 重試 → 最終成功」這類行為軌跡；``False`` 則需與 log 完全等長逐項相符。
        """
        def matches(op: ServerOp, exp: Any) -> bool:
            if isinstance(exp, str):
                return op.command == exp
            if isinstance(exp, tuple):
                for key, val in zip(("command", "result_typ", "response_code"), exp):
                    if val is not None and getattr(op, key) != val:
                        return False
                return True
            if isinstance(exp, dict):
                return all(getattr(op, k) == v for k, v in exp.items())
            raise TypeError(f"不支援的期望項型別：{exp!r}")

        if subsequence:
            it = iter(self.log)
            for exp in expected:
                if not any(matches(op, exp) for op in it):  # 推進迭代器找下一個符合者
                    raise AssertionError(f"序列未對齊：找不到 {exp!r}\n{self.dump()}")
        else:
            if len(self.log) != len(expected) or not all(matches(o, e) for o, e in zip(self.log, expected)):
                raise AssertionError(f"序列不完全相符：期望 {expected!r}\n{self.dump()}")

    def timing_report(self) -> dict:
        """E9/REQ-OBS-A5：每命令的**虛擬**單調時鐘 + 注入延遲（確定性計時故障分析）。

        注意：``t_mono`` 為虛擬時鐘，**只被 ``arm_latency`` 注入的延遲推進**——非實測 RTT 或 throughput
        （記憶體引擎無真實網路往返）。用途是「注入已知延遲 → 驗證產品 timeout/retry 行為」的確定性測試。
        """
        return {
            "ops": [
                {"seq": op.seq, "command": op.command, "t_mono": op.t_mono, "latency_s": op.injected_latency_s}
                for op in self.log
            ],
            "total_injected_latency_s": sum(op.injected_latency_s for op in self.log),
            "slowest_ops": sorted(
                ((op.injected_latency_s, op.command, op.seq) for op in self.log if op.injected_latency_s),
                reverse=True,
            )[:3],
        }

    def assert_state_machine_legal(self) -> None:
        """E9/REQ-OBS-A3：斷言產品驅動的狀態轉移皆合法（防 NONAUTH→SELECTED 之類跳階）。"""
        legal = {
            ("NONAUTH", "AUTH"), ("AUTH", "SELECTED"), ("SELECTED", "SELECTED"),
            ("SELECTED", "AUTH"), ("AUTH", "AUTH"),
        }
        for a, b, cause in self.transitions:
            # 重連（→NONAUTH）與登出（→LOGOUT）自任何狀態皆合法
            if b in ("LOGOUT", "NONAUTH") or (a, b) in legal:
                continue
            raise AssertionError(f"非法狀態轉移 {a} → {b}（{cause}）\n{self.transitions}")

    def dump(self) -> str:
        """除錯用：一次吐 wire transcript + 結構化 log + 快照（失敗時貼上即可定位）。"""
        lines = ["=== wire (C->S / S->C) ==="]
        for b in self.wire_in:
            lines.append(f"C: {b!r}")
        lines.append("--- structured log ---")
        for op in self.log:
            lines.append(repr(op))
        if self.fault_events:
            lines.append("--- fault events ---")
            for fe in self.fault_events:
                lines.append(repr(fe))
        if self.transitions:
            lines.append("--- state transitions ---")
            lines.append(" -> ".join(t[1] for t in self.transitions))
        lines.append("--- snapshot ---")
        for name, msgs in self.snapshot().items():
            lines.append(f"{name}: {[(u, sorted(f)) for u, f in msgs]}")
        return "\n".join(lines)
