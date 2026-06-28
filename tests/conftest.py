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
    """In-memory ``MailBackend`` for offline organizer/cli tests.

    Accepts either a flat ``headers`` list (treated as INBOX, legacy) or a
    ``folders`` dict mapping folder name -> list[MailHeader].
    """

    def __init__(
        self,
        headers: list[MailHeader] | None = None,
        *,
        folders: dict[str, list[MailHeader]] | None = None,
    ) -> None:
        if folders is not None:
            self._folders = {k: list(v) for k, v in folders.items()}
        elif headers is not None:
            self._folders = {"INBOX": list(headers)}
        else:
            self._folders = {"INBOX": [MailHeader("1", "Weekly Newsletter", "news@x.com", "Mon")]}
        self.actions: list[tuple] = []

    def list_folders(self) -> list[str]:
        return list(self._folders.keys())

    def list_headers(self, folder: str = "INBOX", *, on_progress=None) -> list[MailHeader]:
        items = list(self._folders.get(folder, []))
        total = len(items)
        for i, _h in enumerate(items, 1):
            if on_progress is not None:
                on_progress(i, total)
        return items

    def list_uids(self, folder: str = "INBOX", *, on_progress=None) -> set[str]:
        items = list(self._folders.get(folder, []))
        total = len(items)
        for i, _h in enumerate(items, 1):
            if on_progress is not None:
                on_progress(i, total)
        return {h.uid for h in items}

    def list_inbox_headers(self, mailbox: str = "INBOX") -> list[MailHeader]:
        return self.list_headers(mailbox)

    def ensure_folder(self, folder: str) -> None:
        self.actions.append(("folder", folder))
        self._folders.setdefault(folder, [])

    def move(self, uid: str, dest_folder: str, mailbox: str = "INBOX") -> None:
        self.actions.append(("move", uid, dest_folder, mailbox))
        src = self._folders.get(mailbox, [])
        moved = [h for h in src if h.uid == uid]
        self._folders[mailbox] = [h for h in src if h.uid != uid]
        self._folders.setdefault(dest_folder, []).extend(moved)

    def move_many(self, uids, dest_folder: str, mailbox: str = "INBOX") -> dict:
        out: dict = {}
        for uid in uids:
            present = {h.uid for h in self._folders.get(mailbox, [])}
            if uid in present:
                self.move(uid, dest_folder, mailbox)
                out[uid] = None
            else:
                out[uid] = "來源 UID 不存在"
        return out

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
    def _make(
        headers: list[MailHeader] | None = None,
        *,
        folders: dict[str, list[MailHeader]] | None = None,
    ) -> FakeBackend:
        return FakeBackend(headers, folders=folders)

    return _make


@pytest.fixture
def folder_backend(make_backend) -> FakeBackend:
    """多資料夾假信箱：INBOX/Work/Archive，含特殊字元與空資料夾。"""
    return make_backend(
        folders={
            "INBOX": [
                MailHeader("10", "新年快樂 🎉, 與你", "寄件者 <a@x.com>", "Mon", "me@x.com"),
                MailHeader("11", "Plain ASCII", "b@y.com", "Tue", "me@x.com"),
            ],
            "Work": [MailHeader("20", "週報", "boss@x.com", "Wed", "me@x.com")],
            "Archive": [],
        }
    )


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
