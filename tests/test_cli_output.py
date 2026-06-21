"""US1 — pipeline output is crash-proof end to end. Written test-first."""
from __future__ import annotations

import sys

from mailkeeper import cli, console


def test_run_listing_non_utf8_does_not_crash(
    monkeypatch, fake_non_utf8_stdout, make_backend, emoji_headers
):
    monkeypatch.setattr(sys, "stdout", fake_non_utf8_stdout)
    console.setup()
    backend = make_backend(emoji_headers)

    cli.run_listing(backend, [], dry_run=True)

    out = fake_non_utf8_stdout.getvalue()
    assert "(3 " in out  # count line emitted (ASCII part)
    assert "Plain ASCII subject" in out  # ASCII header rendered verbatim
    assert "\\u" in out.lower() or "\\U" in out  # CJK/emoji degraded, not crashed


def test_run_listing_utf8_renders_without_escapes(
    monkeypatch, fake_utf8_stdout, make_backend, emoji_headers
):
    monkeypatch.setattr(sys, "stdout", fake_utf8_stdout)
    console.setup()
    backend = make_backend(emoji_headers)

    cli.run_listing(backend, [], dry_run=True)

    out = fake_utf8_stdout.getvalue()
    assert "新年快樂 🎉" in out  # rendered correctly
    assert "\\u" not in out and "\\U" not in out  # no unnecessary placeholders
