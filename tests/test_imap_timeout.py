"""US3 — IMAP connection is time-bounded (anti-stuck). Test-first.

透過線級 IMAP 引擎 + install_server() 驅動**真 imaplib** 的 connect()，查核傳入 IMAP4_SSL 的 timeout。
（P3：已自舊 FakeIMAPConn 遷移至 imap_server 引擎；上層產品零改動。）
"""
from __future__ import annotations

from imap_dataset import fresh_server
from imap_transport import install_server

from mailkeeper import config
from mailkeeper.imap_client import OutlookIMAPClient


def test_imap_client_uses_default_timeout(monkeypatch):
    server = fresh_server()
    cap = install_server(monkeypatch, server)
    OutlookIMAPClient("a@b.com", "tok").connect()
    assert cap["timeout"] == config.IMAP_TIMEOUT
    assert isinstance(config.IMAP_TIMEOUT, (int, float))
    assert server.commands("AUTHENTICATE")  # 連線後有送出認證


def test_imap_client_uses_overridden_timeout(monkeypatch):
    cap = install_server(monkeypatch, fresh_server())
    OutlookIMAPClient("a@b.com", "tok", timeout=7).connect()
    assert cap["timeout"] == 7
