"""US3 — IMAP connection is time-bounded (anti-stuck). Test-first."""
from __future__ import annotations

from mailkeeper import config
from mailkeeper.imap_client import OutlookIMAPClient


def test_imap_client_uses_configured_timeout(monkeypatch):
    captured: dict = {}

    class FakeIMAP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout

        def authenticate(self, mechanism, authobject):
            captured["auth"] = True

    monkeypatch.setattr("mailkeeper.imap_client.imaplib.IMAP4_SSL", FakeIMAP)

    OutlookIMAPClient("a@b.com", "tok").connect()

    assert captured["timeout"] == config.IMAP_TIMEOUT
    assert isinstance(config.IMAP_TIMEOUT, (int, float))
