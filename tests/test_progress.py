"""US3 — backend-neutral progress reporter. Test-first."""
from __future__ import annotations

import io

import pytest

from mailkeeper import progress


class _Stream(io.StringIO):
    def __init__(self, tty: bool = True) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _drive(cb, total: int, n: int | None = None) -> None:
    n = total if n is None else n
    for i in range(1, n + 1):
        cb(i, total)


def test_shows_when_tty_and_above_threshold():
    s = _Stream(tty=True)
    with progress.reporter("讀取", stream=s) as cb:
        _drive(cb, 50)
    out = s.getvalue()
    assert out and "50/50" in out and out.endswith("\n")  # 顯示 + 乾淨收尾


def test_silent_when_non_tty():
    s = _Stream(tty=False)
    with progress.reporter("讀取", stream=s) as cb:
        _drive(cb, 100)
    assert s.getvalue() == ""  # 非 TTY 零輸出 (FR-010)


def test_silent_when_at_or_below_threshold():
    s = _Stream(tty=True)
    with progress.reporter("讀取", stream=s) as cb:
        _drive(cb, 30)  # total=30，非 > 30
    assert s.getvalue() == ""  # 門檻 (FR-015)


def test_clean_finish_on_exception_and_propagates():
    s = _Stream(tty=True)
    with pytest.raises(ValueError):
        with progress.reporter("讀取", stream=s) as cb:
            cb(10, 50)
            raise ValueError("boom")
    assert s.getvalue().endswith("\n")  # 例外仍乾淨收尾 (FR-012)


def test_swallows_stream_write_error_with_cjk_label():
    class _Bad(_Stream):
        def write(self, _s):  # type: ignore[override]
            raise UnicodeEncodeError("utf-8", "x", 0, 1, "boom")

    s = _Bad(tty=True)
    # CJK/emoji 標籤 + 寫入丟編碼錯誤 → 被吞、不崩潰（編碼安全 FR-011）
    with progress.reporter("讀取中文標籤🎉", stream=s) as cb:
        _drive(cb, 50)  # 不應拋出例外


def test_writes_only_to_injected_stream(capsys):
    s = _Stream(tty=True)
    with progress.reporter("讀取", stream=s) as cb:
        _drive(cb, 50)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""  # 不污染 stdout/stderr (FR-011)


def test_total_none_is_noop():
    s = _Stream(tty=True)
    with progress.reporter("讀取", stream=s) as cb:
        cb(5, None)
        cb(10, None)
    assert s.getvalue() == ""  # 未知總數 → no-op、不崩潰


def test_renders_visual_ascii_bar():
    # 強化：除了 done/total 文字，需有 ASCII 字碼狀態條（外框 + 已完成/未完成填充）。
    s = _Stream(tty=True)
    with progress.reporter("讀取", stream=s) as cb:
        cb(25, 50)  # 50%
        cb(50, 50)  # 100%
    out = s.getvalue()
    assert "[" in out and "]" in out  # 狀態條外框
    assert "█" in out  # 已完成填充
    assert "░" in out  # 未完成填充（50% 時仍有未填滿）
    assert "50/50" in out and "100%" in out


def test_network_loop_shows_even_below_threshold():
    # FR-013：網路 in/out 迴圈不設件數門檻 → 即使 ≤30 也顯示
    s = _Stream(tty=True)
    with progress.reporter("讀取", network=True, stream=s) as cb:
        _drive(cb, 3)
    assert s.getvalue() and "3/3" in s.getvalue()


def test_cpu_loop_silent_below_threshold():
    # FR-013：純 CPU 迴圈維持 >30 才顯示 → 3 筆不顯示
    s = _Stream(tty=True)
    with progress.reporter("讀取", network=False, stream=s) as cb:
        _drive(cb, 3)
    assert s.getvalue() == ""


def test_network_loop_still_silent_on_non_tty():
    s = _Stream(tty=False)
    with progress.reporter("讀取", network=True, stream=s) as cb:
        _drive(cb, 3)
    assert s.getvalue() == ""  # 非 TTY 仍零輸出（不污染管線）


def test_bar_fills_proportionally():
    s = _Stream(tty=True)
    with progress.reporter("讀取", stream=s) as cb:
        cb(50, 50)  # 100% → 整條填滿，無未完成字元
    last_frame = s.getvalue().split("\r")[-1]
    assert "░" not in last_frame  # 100% 不應再有未完成填充
    assert "█" in last_frame
