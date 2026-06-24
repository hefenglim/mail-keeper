"""共用 IMAP wire 助手 + 模擬郵件模型（離線測試地基的共用件）。

歷史：本檔曾承載 ``FakeIMAPConn``（假裝成 imaplib 客戶端的高階假物）。P3 完成後，模擬器已全面
改為**線級伺服器引擎**（``imap_server.ImapServer`` + 真 ``imaplib.IMAP4_SSL``，見
``imap_transport``），FakeIMAPConn 已退場。本檔僅保留**引擎與母版資料集共用**的純函式與資料模型：
  * ``SimMessage`` / ``message``：模擬郵件模型（uid / 表頭 / 旗標）。
  * ``_encode_mutf7``：資料夾名 → modified-UTF-7（對應產品 ``imap_client._decode_mutf7``）。
  * ``_render_header_literal`` / ``_encode_header_value``：FETCH 表頭 literal 的**單一可信序列化**。
  * ``_parse_uidset`` / ``_unquote`` / ``_HEADER_TITLE``：UID 集合解析、去引號、表頭標題對照。
  * ``DELETED`` / ``SEEN`` / ``FLAGGED``：旗標常數。
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any, Optional

DELETED = "\\Deleted"  # 實際字串為 \Deleted（單一反斜線）
SEEN = "\\Seen"
FLAGGED = "\\Flagged"


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
    """imaplib 會自動為含特殊字元的信箱名加引號；引擎收端去引號還原。"""
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


def _encode_word(tok: str) -> str:
    return "=?UTF-8?B?" + base64.b64encode(tok.encode("utf-8")).decode("ascii") + "?="


def _encode_header_value(value: str) -> str:
    """ASCII 直接輸出；含非 ASCII → **逐空白分隔詞** RFC 2047 編碼。

    擬真硬化：真實 MUA 只把含非 ASCII 的「詞」編成 encoded-word，ASCII 詞（如 email 位址）保持
    原樣——例 ``"王經理 <boss@x.com>"`` → ``"=?UTF-8?B?..?= <boss@x.com>"``（只編顯示名）。
    FETCH 表頭 literal 的**單一可信編碼器**（``imap_server.ImapServer`` 經 ``_render_header_literal`` 共用）。
    """
    try:
        value.encode("ascii")
        return value  # 全 ASCII → 原樣
    except UnicodeEncodeError:
        pass
    out: list[str] = []
    for tok in value.split(" "):
        try:
            tok.encode("ascii")
            out.append(tok)  # ASCII 詞（含 email 位址）保持原樣
        except UnicodeEncodeError:
            out.append(_encode_word(tok))  # 僅含非 ASCII 的詞才編碼
    return " ".join(out)


def _fold_header_line(line: str) -> str:
    """RFC 5322 表頭折行：超過 78 字元且有空白時，於空白處插入 ``CRLF + 空白`` 續行。

    擬真硬化：長表頭在真實郵件會折行；產品端 ``imap_client._unfold`` 會把續行還原為單一空白，
    本折行正是驅動該還原路徑（折在既有空白處 → unfold 後與原值逐字相符）。
    """
    if len(line) <= 78 or " " not in line:
        return line
    out: list[str] = []
    cur = ""
    for word in line.split(" "):
        if cur and len(cur) + 1 + len(word) > 78:
            out.append(cur)
            cur = word
        else:
            cur = word if not cur else cur + " " + word
    out.append(cur)
    return "\r\n ".join(out)  # 續行以單一空白開頭（折疊空白）


def _render_header_literal(m: "SimMessage", section: str) -> bytes:
    """產生 ``BODY[HEADER.FIELDS (...)]`` 的 literal：依索取欄位輸出、結尾空行（單一可信來源）。

    非 ASCII 值逐詞以 RFC 2047 encoded-word 編碼（真實郵件即如此存放，非裸 UTF-8）；長表頭折行
    （驅動產品 ``_unfold``）。皆確保產品端解碼路徑被真實位元組流驅動；空值欄位（如空主旨）略過。
    """
    fm = re.search(r"HEADER\.FIELDS\s*\(([^)]*)\)", section, re.IGNORECASE)
    names = fm.group(1).split() if fm else list(m.fields.keys())
    lines = []
    for raw in names:
        key = raw.upper()
        if key in m.fields and m.fields[key]:
            title = _HEADER_TITLE.get(key, raw)
            lines.append(_fold_header_line(f"{title}: {_encode_header_value(m.fields[key])}"))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
