"""郵件整理模組 —— 定義整理規則與執行邏輯。

未來調整「整理需求」時，主要改這個檔案 (以及 main.build_rules)，
不會動到 IMAP 連線層 (imap_client.py)。

上層只依賴 MailBackend 這個介面，所以底層換成 Graph API 也不影響這裡。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .imap_client import MailHeader


class MailBackend(Protocol):
    """郵件後端介面。IMAP / Graph 任一實作只要符合這些方法即可互換。"""

    def list_folders(self) -> list[str]: ...
    def list_headers(self, folder: str = "INBOX") -> list[MailHeader]: ...
    def list_inbox_headers(self, mailbox: str = "INBOX") -> list[MailHeader]: ...
    def ensure_folder(self, folder: str) -> None: ...
    def move(self, uid: str, dest_folder: str, mailbox: str = "INBOX") -> None: ...
    def mark_read(self, uid: str, mailbox: str = "INBOX") -> None: ...
    def flag(self, uid: str, mailbox: str = "INBOX") -> None: ...


@dataclass
class Rule:
    """單一整理規則：符合 match 條件時執行對應動作。"""

    name: str
    match: Callable[[MailHeader], bool]
    dest_folder: str | None = None  # 要搬去的資料夾 (None = 不搬移)
    mark_read: bool = False
    flag: bool = False


# ---- 常用條件產生器 (可自由組合) ----
def from_contains(keyword: str) -> Callable[[MailHeader], bool]:
    kw = keyword.lower()
    return lambda h: kw in h.sender.lower()


def subject_contains(keyword: str) -> Callable[[MailHeader], bool]:
    kw = keyword.lower()
    return lambda h: kw in h.subject.lower()


class MailOrganizer:
    def __init__(self, backend: MailBackend, rules: list[Rule]) -> None:
        self._backend = backend
        self._rules = rules

    def run(self, dry_run: bool = True) -> None:
        """套用規則。dry_run=True 時只顯示命中結果、不實際變更信箱。"""
        headers = self._backend.list_inbox_headers()
        print(f"\n收件匣共 {len(headers)} 封，開始套用 {len(self._rules)} 條規則"
              f"{'（試跑，不會變更）' if dry_run else ''}：\n")

        for h in headers:
            for rule in self._rules:
                if not rule.match(h):
                    continue
                print(f"  [{rule.name}] 命中：{h.subject[:50]}  <{h.sender}>")
                if not dry_run:
                    if rule.dest_folder:
                        self._backend.ensure_folder(rule.dest_folder)
                        self._backend.move(h.uid, rule.dest_folder)
                    if rule.mark_read:
                        self._backend.mark_read(h.uid)
                    if rule.flag:
                        self._backend.flag(h.uid)
                break  # 一封信只套用第一個命中的規則
