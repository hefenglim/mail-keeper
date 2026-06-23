"""母版電子郵件資料集 —— 離線測試的單一可信來源。

涵蓋測試需驗證的各種情境（ASCII / CJK / emoji / 帶引號顯示名 / 已讀 / 使用者已標刪 /
空主旨 / 超長主旨 / 收件者 / 巢狀夾 / CJK 夾名）。每次 :func:`master_mailboxes` 都建構**全新**
物件（深拷貝語意），故各 :func:`fresh_sim` 互相獨立、互不汙染。測試一律從此母版複製一份出發，
之後可雙層驗證：(1) 指令動作日誌 ``sim.log``、(2) ``sim.snapshot()`` 前後資料變動。
"""
from __future__ import annotations

from imap_sim import DELETED, SEEN, FakeIMAPConn, SimMessage, message

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


def fresh_sim(**opts) -> FakeIMAPConn:
    """從母版複製一份，建構一個獨立的 FakeIMAPConn（opts 透傳：supports_move/uidplus 等）。"""
    return FakeIMAPConn(master_mailboxes(), **opts)
