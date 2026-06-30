"""MailKeeper —— 替你看管收件匣的 Outlook.com IMAP 郵件整理工具。"""
from __future__ import annotations

__version__ = "0.6.8"

from .domain import MailHeader
from .imap_client import OutlookIMAPClient
from .organizer import MailOrganizer, Rule, from_contains, subject_contains

__all__ = [
    "__version__",
    "MailHeader",
    "OutlookIMAPClient",
    "MailOrganizer",
    "Rule",
    "from_contains",
    "subject_contains",
]
