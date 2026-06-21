"""US1 — crash-proof output. Written test-first (these fail until console.py exists)."""
from __future__ import annotations

import sys

from mailkeeper import console


def test_safe_print_to_non_utf8_stdout_does_not_raise(fake_non_utf8_stdout):
    # cp1252 cannot encode these — must not raise.
    console.safe_print("收件匣 🎉 piano", file=fake_non_utf8_stdout)
    out = fake_non_utf8_stdout.getvalue()
    assert out.endswith("\n")
    assert "piano" in out  # the ASCII part survives


def test_safe_print_escapes_unrepresentable_chars(fake_non_utf8_stdout):
    console.safe_print("emoji=🎉", file=fake_non_utf8_stdout)
    out = fake_non_utf8_stdout.getvalue()
    assert "emoji=" in out
    assert "\\U0001f389" in out  # backslash-escaped placeholder


def test_safe_print_utf8_renders_without_escapes(fake_utf8_stdout):
    console.safe_print("收件匣 🎉", file=fake_utf8_stdout)
    out = fake_utf8_stdout.getvalue()
    assert "收件匣 🎉" in out
    assert "\\" not in out


def test_setup_then_plain_print_is_safe(monkeypatch, fake_non_utf8_stdout):
    monkeypatch.setattr(sys, "stdout", fake_non_utf8_stdout)
    console.setup()
    # plain print() must now be safe even though the underlying stream is cp1252
    print("收件匣 🎉")
    out = fake_non_utf8_stdout.getvalue()
    assert out  # emitted something
    assert "\\u6536" in out  # 收 (U+6536) escaped, not crashed
