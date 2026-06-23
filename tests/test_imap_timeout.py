"""US3 — IMAP connection is time-bounded (anti-stuck). Test-first.

透過 FakeIMAPConn + install() 驅動真實 connect()，查核傳入 IMAP4_SSL 的 timeout。
"""
from __future__ import annotations

from imap_dataset import fresh_sim
from imap_sim import install

from mailkeeper import config
from mailkeeper.imap_client import OutlookIMAPClient


def test_imap_client_uses_default_timeout(monkeypatch):
    sim = fresh_sim()
    cap = install(monkeypatch, sim)
    OutlookIMAPClient("a@b.com", "tok").connect()
    assert cap["timeout"] == config.IMAP_TIMEOUT
    assert isinstance(config.IMAP_TIMEOUT, (int, float))
    assert sim.commands("authenticate")  # 連線後有送出認證


def test_imap_client_uses_overridden_timeout(monkeypatch):
    cap = install(monkeypatch, fresh_sim())
    OutlookIMAPClient("a@b.com", "tok", timeout=7).connect()
    assert cap["timeout"] == 7
