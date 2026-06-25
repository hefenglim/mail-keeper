"""母版電子郵件資料集 —— 離線測試的單一可信來源。

涵蓋測試需驗證的各種情境（ASCII / CJK / emoji / 帶引號顯示名 / 已讀 / 使用者已標刪 /
空主旨 / 超長主旨 / 收件者 / 巢狀夾 / CJK 夾名）。每次 :func:`master_mailboxes` 都建構**全新**
物件（深拷貝語意），故各 :func:`fresh_server` 互相獨立、互不汙染。測試一律從此母版複製一份出發，
之後可雙層驗證：(1) 結構化命令日誌 ``server.log``、(2) ``server.snapshot()`` 前後資料變動。
"""
from __future__ import annotations

from imap_sim import DELETED, SEEN, SimMessage, message, mime_message

# 具名常數：測試引用「眾所周知」的郵件，避免魔術數字。
INBOX_NEWSLETTER_UID = 101
INBOX_CJK_UID = 102
INBOX_EMOJI_UID = 103
INBOX_QUOTED_FROM_UID = 104
INBOX_SEEN_UID = 105
INBOX_USER_DELETED_UID = 106  # 使用者自己標 \Deleted —— 驗證搬移不可波及它
INBOX_EMPTY_SUBJECT_UID = 107
INBOX_LONG_SUBJECT_UID = 108


def master_mailboxes() -> dict[str, list[SimMessage]]:
    """回傳一份全新的母版信箱資料（每次呼叫都是獨立物件，可安全變動）。"""
    return {
        "INBOX": [
            message(INBOX_NEWSLETTER_UID, "Weekly Newsletter", "news@x.com", "me@outlook.my", "Mon, 1 Jan 2026"),
            message(INBOX_CJK_UID, "週報 Q1 報告", "王經理 <boss@x.com>", "me@outlook.my", "Tue, 2 Jan 2026"),
            message(INBOX_EMOJI_UID, "🎉 Happy New Year 新年快樂", "friend@x.com", "me@outlook.my", "Wed"),
            message(
                INBOX_QUOTED_FROM_UID,
                "FW: 推薦職務",
                '"Serena Yeh" <serena@etalent.com.tw>',
                "<kevin@outlook.my>",
                "Thu",
            ),
            message(INBOX_SEEN_UID, "Already read", "a@x.com", "me@outlook.my", "Fri", flags={SEEN}),
            message(INBOX_USER_DELETED_UID, "User marked delete", "b@x.com", "me@outlook.my", "Sat", flags={DELETED}),
            message(INBOX_EMPTY_SUBJECT_UID, "", "c@x.com", "me@outlook.my", "Sun"),
            message(INBOX_LONG_SUBJECT_UID, "L" * 200, "d@x.com", "me@outlook.my", "Mon"),
        ],
        "Sent": [
            message(201, "Re: 報告", "me@outlook.my", "boss@x.com", "Tue"),
        ],
        "Archive": [],  # 空夾：常見的搬移目標
        "Work/Projects": [  # 巢狀夾名
            message(301, "Project kickoff", "pm@x.com", "me@outlook.my", "Wed"),
        ],
        "台北": [  # CJK 夾名（modified-UTF-7 保真）
            message(401, "在地通知", "local@x.com", "me@outlook.my", "Thu"),
        ],
    }


def fresh_server(**opts):
    """從母版複製一份，建構一個獨立的 ``imap_server.ImapServer``（線級引擎；opts 透傳
    supports_move/uidplus 等）。搭配 ``imap_transport.install_server``/``connected_client``，
    讓**真 imaplib** 跑在引擎之上——P3 起所有產品行為測試的單一可信入口。"""
    from imap_server import ImapServer

    return ImapServer(master_mailboxes(), **opts)


def bulk_mailboxes(n: int = 120) -> dict[str, list[SimMessage]]:
    """大量郵件母版（>100 封）—— 驅動產品 ``_FETCH_BATCH=50`` 的**多批 UID FETCH** 與進度回報。

    uid 自 1000 連續編號；含一封 CJK 主旨以確保多批路徑也經 encoded-word 解碼。
    """
    msgs = [
        message(1000 + i, f"Bulk message {i}", f"sender{i}@x.com", "me@outlook.my", "Mon, 1 Jan 2026")
        for i in range(n)
    ]
    if n > 0:
        msgs[n // 2] = message(1000 + n // 2, "批量信件 CJK", "寄件者 <bulk@x.com>", "me@outlook.my", "Tue")
    return {"INBOX": msgs, "Archive": []}


def bulk_server(n: int = 120, **opts):
    """大量郵件的 ``ImapServer``（驅動多批 FETCH；opts 透傳）。"""
    from imap_server import ImapServer

    return ImapServer(bulk_mailboxes(n), **opts)


# ── E11：帶 MIME 內文 / 附件的母版（驅動 BODY[]/BODY[TEXT]/RFC822/RFC822.SIZE/BODYSTRUCTURE）──
MIME_PLAIN_UID = 501       # 純文字 text/plain
MIME_ALT_UID = 502         # multipart/alternative（text + html），含 CJK
MIME_ATTACH_UID = 503      # multipart/mixed（text + 附件）


def mime_mailboxes() -> dict[str, list[SimMessage]]:
    """帶完整 RFC822 內文的母版（每次呼叫皆全新物件）。INBOX 同母版以維持既有計數不變。"""
    return {
        "INBOX": [
            mime_message(
                MIME_PLAIN_UID, "Plain note", "alice@x.com", "me@outlook.my", "Mon, 1 Jan 2026",
                text="Hello world.\nSecond line.\n",
            ),
            mime_message(
                MIME_ALT_UID, "週報 內文", "王經理 <boss@x.com>", "me@outlook.my", "Tue, 2 Jan 2026",
                text="純文字版本\n", html="<p>HTML 版本</p>",
            ),
            mime_message(
                MIME_ATTACH_UID, "With attachment", "bob@x.com", "me@outlook.my", "Wed, 3 Jan 2026",
                text="See attached.\n",
                attachments=[("report.csv", b"a,b,c\r\n1,2,3\r\n", "text", "csv")],
            ),
        ],
        "Archive": [],
    }


def mime_server(**opts):
    """帶 MIME 內文/附件的 ``ImapServer``（E11；opts 透傳）。"""
    from imap_server import ImapServer

    return ImapServer(mime_mailboxes(), **opts)
