"""Shared offline test fixtures for MailKeeper.

No network, no real IMAP/auth. The fixtures here let us exercise the
presentation, decoding, and failure-handling behavior deterministically.
"""
from __future__ import annotations

import io

import pytest

from mailkeeper.imap_client import MailHeader


class EncodingBoundStringIO:
    """A text-like stream that encodes to a fixed code page on write,
    mimicking a real console/pipe. Writing a character the code page
    cannot represent raises ``UnicodeEncodeError`` (as cp1252/cp950 do)."""

    def __init__(self, encoding: str = "cp1252") -> None:
        self.encoding = encoding
        self.errors = "strict"
        self._buf = io.BytesIO()

    def write(self, s: str) -> int:
        self._buf.write(s.encode(self.encoding, self.errors))
        return len(s)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return self._buf.getvalue().decode(self.encoding, "replace")


class FakeBackend:
    """In-memory ``MailBackend`` for offline organizer/cli tests."""

    def __init__(self, headers: list[MailHeader] | None = None) -> None:
        self._headers = (
            headers
            if headers is not None
            else [MailHeader("1", "Weekly Newsletter", "news@x.com", "Mon")]
        )
        self.actions: list[tuple] = []

    def list_inbox_headers(self, mailbox: str = "INBOX") -> list[MailHeader]:
        return list(self._headers)

    def ensure_folder(self, folder: str) -> None:
        self.actions.append(("folder", folder))

    def move(self, uid: str, dest_folder: str, mailbox: str = "INBOX") -> None:
        self.actions.append(("move", uid, dest_folder))

    def mark_read(self, uid: str, mailbox: str = "INBOX") -> None:
        self.actions.append(("read", uid))

    def flag(self, uid: str, mailbox: str = "INBOX") -> None:
        self.actions.append(("flag", uid))


@pytest.fixture
def fake_non_utf8_stdout() -> EncodingBoundStringIO:
    """A stdout that cannot represent non-cp1252 characters (CJK/emoji)."""
    return EncodingBoundStringIO("cp1252")


@pytest.fixture
def fake_utf8_stdout() -> EncodingBoundStringIO:
    """A stdout that can represent any character."""
    return EncodingBoundStringIO("utf-8")


@pytest.fixture
def make_backend():
    def _make(headers: list[MailHeader] | None = None) -> FakeBackend:
        return FakeBackend(headers)

    return _make


@pytest.fixture
def emoji_headers() -> list[MailHeader]:
    return [
        MailHeader("1", "新年快樂 🎉", "寄件者 <a@x.com>", "Mon, 1 Jan 2026"),
        MailHeader("2", "피망플러스 안내 💻", "news@x.com", "Tue"),
        MailHeader("3", "Plain ASCII subject", "b@y.com", "Wed"),
    ]


@pytest.fixture
def tmp_cwd(tmp_path, monkeypatch):
    """以暫存目錄作為當前工作目錄 (config.json / token_cache 解析基準)。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path
