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
import email.policy
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Optional, Sequence

DELETED = "\\Deleted"  # 實際字串為 \Deleted（單一反斜線）
SEEN = "\\Seen"
FLAGGED = "\\Flagged"


@dataclass
class SimMessage:
    """模擬器底層的一封郵件。

    ``fields``：HEADER.FIELDS FETCH 用的表頭值（產品讀標題的路徑）。
    ``raw``：整封 RFC822 bytes（E11，MIME 內文/附件建模）；header-only 訊息為 ``None``，
    引擎抓 ``BODY[]``/``RFC822`` 時退回「全表頭 + 空行」。建構詳見 :func:`mime_message`。
    """

    uid: int
    fields: dict[str, str] = field(default_factory=dict)  # 表頭名（大寫）-> 值
    flags: set[str] = field(default_factory=set)
    raw: Optional[bytes] = None


_DEFAULT_MID = object()  # sentinel：未指定 → 用穩定預設 Message-ID；明確 None → 該封無 Message-ID


def message(
    uid: int,
    subject: str = "",
    sender: str = "",
    to: str = "",
    date: str = "",
    *,
    flags: Optional[set[str]] = None,
    message_id: object = _DEFAULT_MID,
) -> SimMessage:
    """建構一封模擬郵件的便捷函式（header-only：只帶 HEADER.FIELDS 可取的表頭）。

    ``message_id=None`` → 該封**不帶 Message-ID**（罕見但合法的真實郵件；驅動後備搬移在無
    Message-ID 時的去重退化路徑 ``_message_id``/``_dest_has_copy``）。未指定則用穩定預設值。
    """
    mid = f"<msg-{uid}@mailkeeper.test>" if message_id is _DEFAULT_MID else (message_id or "")
    return SimMessage(
        uid,
        {
            "SUBJECT": subject, "FROM": sender, "TO": to, "DATE": date,
            "MESSAGE-ID": mid,  # 穩定唯一識別（後備搬移冪等以此去重）；"" → 渲染時略過（無此表頭）
        },
        set(flags or set()),
    )


def mime_message(
    uid: int,
    subject: str = "",
    sender: str = "",
    to: str = "",
    date: str = "",
    *,
    text: Optional[str] = None,
    html: Optional[str] = None,
    attachments: Optional[Sequence[tuple]] = None,
    flags: Optional[set[str]] = None,
) -> SimMessage:
    """建構一封**帶完整 RFC822 內文**的模擬郵件（E11）。

    以 stdlib :class:`email.message.EmailMessage` 組裝真實郵件（非 ASCII 表頭自動編成 encoded-word、
    內文依內容選 CTE），以 ``email.policy.SMTP``（CRLF 行尾）序列化為 ``raw`` bytes——即真實伺服器
    在 ``BODY[]``/``RFC822`` 會回的位元組。``text``+``html`` → multipart/alternative；有 ``attachments``
    → multipart/mixed。``attachments`` 每項為 ``(filename, data:bytes[, maintype, subtype])``。
    ``fields`` 與表頭同源 → HEADER.FIELDS 路徑與整封內文一致。
    """
    msg = EmailMessage()
    mid = f"<msg-{uid}@mailkeeper.test>"
    msg["Message-ID"] = mid  # 穩定唯一識別（後備搬移冪等以此去重）
    if subject:
        msg["Subject"] = subject
    if sender:
        msg["From"] = sender
    if to:
        msg["To"] = to
    if date:
        msg["Date"] = date
    if text is None and html is None:
        text = ""
    if text is not None:
        msg.set_content(text)
    if html is not None:
        if text is not None:
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")
    for att in attachments or []:
        filename, data = att[0], att[1]
        maintype, subtype = (att[2], att[3]) if len(att) >= 4 else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    raw = msg.as_bytes(policy=email.policy.SMTP)  # CRLF 行尾、標準折行/編碼——真實 wire bytes
    return SimMessage(
        uid,
        {"SUBJECT": subject, "FROM": sender, "TO": to, "DATE": date, "MESSAGE-ID": mid},
        set(flags or set()),
        raw,
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


def _decode_mutf7(name: str) -> str:
    """IMAP modified-UTF-7 解碼（RFC 3501 §5.1.3）—— :func:`_encode_mutf7` 的逆；引擎收端用以
    把產品送來的（已編碼）信箱名還原為邏輯名以比對 mailbox dict。對應產品 ``imap_client._decode_mutf7``。"""
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


def _decode_mailbox_arg(raw: str) -> str | None:
    """解析 SELECT/EXAMINE/CREATE/COPY/MOVE 的信箱名引數（鏡像產品 ``_mailbox_arg``）。

    * quoted-string（``"..."``）→ 去引號 + 反跳脫（``\\"``/``\\\\``）+ 解 mUTF-7。
    * atom（無引號、無空白）→ 解 mUTF-7。
    * **未加引號卻含空白** → 違反 RFC 3501（mailbox name 須為 atom 或 quoted-string）→ 回 ``None``，
      呼叫端應回 ``BAD``。此保真讓引擎能抓出「外送夾名未加引號」這類產品 bug（如 Outlook 內建
      ``Junk Email``/``Deleted Items``），真實伺服器同樣拒絕。
    """
    raw = raw.strip()
    if raw[:1] == '"':
        if len(raw) < 2 or raw[-1] != '"':
            return None  # 引號未閉合 → 語法錯誤
        inner = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return _decode_mutf7(inner)
    if " " in raw:
        return None      # 未加引號卻含空白 → 拒絕
    return _decode_mutf7(raw)


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


_HEADER_TITLE = {
    "SUBJECT": "Subject", "FROM": "From", "TO": "To", "DATE": "Date", "MESSAGE-ID": "Message-ID",
}


def _encode_word(tok: str) -> str:
    return "=?UTF-8?B?" + base64.b64encode(tok.encode("utf-8")).decode("ascii") + "?="


def _encode_header_value(value: str) -> str:
    """ASCII 直接輸出；含非 ASCII → RFC 2047 編碼**第一個到最後一個非 ASCII 字元的整段**為單一
    encoded-word，前後純 ASCII 部分保持原樣。

    擬真硬化：真實 MUA 把含非 ASCII 的顯示段編成 encoded-word，尾端 ASCII（如 email 位址）保留——
    例 ``"王經理 <boss@x.com>"`` → ``"=?UTF-8?B?..?= <boss@x.com>"``（只編顯示名）。

    **關鍵正確性**：整段（含其間空白）編進**單一** encoded-word，空白被 base64 保留；切勿拆成多個
    相鄰 encoded-word——RFC 2047 規定 ``decode_header`` 會吃掉相鄰 encoded-word 間的空白，導致
    ``"週報 報告"`` 還原成 ``"週報報告"``（空白遺失）。FETCH 表頭 literal 的**單一可信編碼器**
    （``imap_server.ImapServer`` 經 ``_render_header_literal`` 共用）。
    """
    try:
        value.encode("ascii")
        return value  # 全 ASCII → 原樣
    except UnicodeEncodeError:
        pass
    nonascii = [i for i, ch in enumerate(value) if ord(ch) > 0x7F]
    lo, hi = nonascii[0], nonascii[-1] + 1  # 涵蓋首尾非 ASCII 之間的所有字元（含 ASCII 與空白）
    return value[:lo] + _encode_word(value[lo:hi]) + value[hi:]


def _fold_header_line(line: str) -> str:
    """RFC 5322 表頭折行：超過 78 字元時於**值內**的空白處插入 ``CRLF + 空白`` 續行。

    擬真硬化：長表頭在真實郵件會折行；產品端 ``imap_client._unfold`` 會把續行還原為單一空白，
    本折行正是驅動該還原路徑（折在既有空白處 → unfold 後與原值逐字相符）。

    **保真鐵則（RFC 5322 §2.2.3）**：折行只能在**既有空白**處，故
      * **單一無內部空白的長 token 不可折**（如 200 字元主旨）——真實伺服器會送一整行長表頭；
      * **絕不在欄名（``Subject:``）後立即折**，否則整個值被推到續行 → 不同 Python 版本的 ``email``
        解析折疊值時對前導空白處理不一（3.10 保留、3.12 去除），造成 ``" L…"`` vs ``"L…"`` 的版本差。
    因此「頭段」恆含「欄名 + 首個**非空**值詞」（即使值以空白起始、含連續空白亦然），只在其後折行——
    保證值**永不落在續行**，消除上述版本依賴。
    """
    if len(line) <= 78:
        return line
    words = line.split(" ")
    # 頭段須含欄名 + 首個非空值詞；跳過連續空白造成的空字串（值前導空白原樣保留於頭段、不折開）。
    head_end = 1
    while head_end < len(words) and words[head_end] == "":
        head_end += 1
    head_end += 1  # 納入首個非空值詞
    if head_end >= len(words):  # 頭段之後已無可折空白（單一無內部空白 token）→ 不折
        return line
    out: list[str] = []
    cur = " ".join(words[:head_end])  # 欄名 +（含原樣連續空白的）首詞——不可折開
    for word in words[head_end:]:
        if len(cur) + 1 + len(word) > 78:
            out.append(cur)
            cur = word
        else:
            cur = cur + " " + word
    out.append(cur)
    return "\r\n ".join(out)  # 續行以單一空白開頭（折疊空白）


def _fold_header_line_noncompliant(line: str) -> str:
    """**不合規折行（刻意異常注入）**：在欄名冒號後立即折行，把整個值推到續行。

    某些不合規/老舊伺服器確有此行為；它使「值起始於續行」——產品 ``_unfold`` 還原時，續行的前導
    折疊空白會浮現，且不同 Python 的 ``email`` 函式庫對此處理不一（這正是 0.x 折行保真 bug 在 3.10
    上現形的成因）。本注入即把該真實異常情景**確定性化**：對同一輸入恆產生同一 bytes（版本無關），
    版本差異只發生在「產品如何解讀」，由測試以**內容比對（容忍前導空白）**斷言產品異常路徑穩健還原。
    """
    name, sep, value = line.partition(":")
    if not sep or not value.strip():
        return line  # 無值（如空主旨）→ 不折
    return f"{name}:\r\n {value.strip()}"  # 值整段移到續行（續行以折疊空白起始）


def _render_header_literal(m: "SimMessage", section: str, *, malformed_fold: bool = False) -> bytes:
    """產生 ``BODY[HEADER.FIELDS (...)]`` 的 literal：依索取欄位輸出、結尾空行（單一可信來源）。

    非 ASCII 值逐詞以 RFC 2047 encoded-word 編碼（真實郵件即如此存放，非裸 UTF-8）；長表頭折行
    （驅動產品 ``_unfold``）。皆確保產品端解碼路徑被真實位元組流驅動；空值欄位（如空主旨）略過。

    ``malformed_fold=True``（確定性異常注入）：改用 :func:`_fold_header_line_noncompliant`——欄名後
    立即折行、值落在續行，用以驗證產品對不合規折行的異常處理路徑能否穩健還原內容。
    """
    folder = _fold_header_line_noncompliant if malformed_fold else _fold_header_line
    fm = re.search(r"HEADER\.FIELDS\s*\(([^)]*)\)", section, re.IGNORECASE)
    names = fm.group(1).split() if fm else list(m.fields.keys())
    lines = []
    for raw in names:
        key = raw.upper()
        if key in m.fields and m.fields[key]:
            title = _HEADER_TITLE.get(key, raw)
            lines.append(folder(f"{title}: {_encode_header_value(m.fields[key])}"))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
