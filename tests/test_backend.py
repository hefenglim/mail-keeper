"""Foundational backend tests — MailHeader.recipients + folder-name parsing. Test-first."""
from __future__ import annotations

import pytest

from mailkeeper.imap_client import MailHeader, _parse_folder_name


def test_mailheader_recipients_appended_last_with_default():
    # 既有 4 位置引數仍對映到 uid/subject/sender/date；recipients 預設 ""
    h = MailHeader("1", "Subj", "from@x.com", "Mon")
    assert (h.uid, h.subject, h.sender, h.date) == ("1", "Subj", "from@x.com", "Mon")
    assert h.recipients == ""


def test_mailheader_recipients_settable():
    h = MailHeader("2", "S", "f@x.com", "Tue", "to@x.com")
    assert h.recipients == "to@x.com"


@pytest.mark.parametrize(
    "line,expected",
    [
        (b'(\\HasNoChildren) "/" "INBOX"', "INBOX"),
        (b'(\\HasNoChildren) "/" INBOX', "INBOX"),
        (b'(\\HasNoChildren) "/" "Work/Projects"', "Work/Projects"),
        (b'(\\HasNoChildren) "/" "Has Space"', "Has Space"),
        # modified UTF-7 (RFC 3501 example: "&U,BTFw-" == 台北)
        (b'(\\HasNoChildren) "/" "&U,BTFw-"', "台北"),
    ],
)
def test_parse_folder_name(line, expected):
    assert _parse_folder_name(line) == expected
