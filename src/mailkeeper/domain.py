"""後端中立的領域型別與契約 —— 上層與任一後端（IMAP / Graph）共同依賴的穩定 seam。

放在此中性模組（而非 `imap_client`）的用意（SR F8 / 憲法 Principle I）：新增後端（如未來的
`graph_client`）或任何上層模組都從這裡 import `MailHeader` / `ReauthRequired` / `MailBackend`，
**不必反向相依任一後端實作**，使「換後端」真正 trivial。本模組**不** import 任何後端、imaplib 或 MSAL。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class ReauthRequired(Exception):
    """需使用者重新登入的終結訊號（非暫時性、不重試）。

    後端中立：定義於此使 `imap_client` 不必 import MSAL、`auth` 不必 import 後端（Principle I）。
    訊息**絕不**含 token/secret（Principle IV）。
    """


@dataclass(frozen=True)
class MailHeader:
    """一封郵件的標題資訊（上層只看得到這個，看不到 imaplib）。"""

    uid: str
    subject: str
    sender: str
    date: str
    recipients: str = ""


class MailBackend(Protocol):
    """郵件後端介面。IMAP / Graph 任一實作只要符合這些方法即可互換（穩定 seam）。"""

    def list_folders(self) -> list[str]: ...
    def list_headers(
        self, folder: str = "INBOX", *, on_progress: Callable[[int, int], None] | None = None
    ) -> list[MailHeader]: ...
    def list_uids(
        self, folder: str = "INBOX", *, on_progress: Callable[[int, int], None] | None = None
    ) -> set[str]: ...
    def list_inbox_headers(self, mailbox: str = "INBOX") -> list[MailHeader]: ...
    def ensure_folder(self, folder: str) -> None: ...
    def move(self, uid: str, dest_folder: str, mailbox: str = "INBOX") -> None: ...
    def move_many(
        self, uids: list[str], dest_folder: str, mailbox: str = "INBOX"
    ) -> dict[str, str | None]: ...
    def mark_read(self, uid: str, mailbox: str = "INBOX") -> None: ...
    def flag(self, uid: str, mailbox: str = "INBOX") -> None: ...
