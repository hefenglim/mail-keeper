"""後端中立的進度回報：大迴圈即時顯示處理進度，避免使用者誤判當機。

`reporter(label)` 回傳一個 `(done, total)` 回呼；僅在互動 TTY 且 `total > threshold`（預設 30）
時就地（`\\r`）顯示，否則為 no-op。離開 context 時乾淨收尾（補換行）；輸出層任何例外都被吞掉，
進度永不使主流程崩潰、不阻塞（憲法 Principle VI）。預設串流為 `sys.stderr`（經 `console` 包裝後
為編碼安全），故進度不污染 stdout 的 CSV/資料輸出。
"""
from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, TextIO

# 後端中立的進度回呼：以 (已處理數, 總數) 呼叫。
ProgressCallback = Callable[[int, int], None]

_THRESHOLD = 30
_MIN_INTERVAL = 0.1  # 重繪節流（秒），避免洗版


class _Progress:
    def __init__(self, label: str, stream: TextIO, threshold: int) -> None:
        self._label = label
        self._stream = stream
        self._threshold = threshold
        self._enabled: Optional[bool] = None
        self._last = 0.0
        self._wrote = False

    def update(self, done: int, total: Optional[int]) -> None:
        try:
            if self._enabled is None:
                isatty = bool(getattr(self._stream, "isatty", lambda: False)())
                self._enabled = total is not None and total > self._threshold and isatty
            if not self._enabled or total is None:
                return
            done = min(done, total)
            now = time.monotonic()
            if done < total and (now - self._last) < _MIN_INTERVAL:
                return  # 節流（最後一筆不節流，確保收尾到 100%）
            self._last = now
            pct = int(done * 100 / total) if total else 100
            self._stream.write(f"\r{self._label} {done}/{total} ({pct}%)")
            self._stream.flush()
            self._wrote = True
        except Exception:
            pass  # 進度永不崩潰主流程

    def close(self) -> None:
        try:
            if self._wrote:
                self._stream.write("\n")
                self._stream.flush()
        except Exception:
            pass


@contextmanager
def reporter(
    label: str, *, stream: Optional[TextIO] = None, threshold: int = _THRESHOLD
) -> Iterator[ProgressCallback]:
    """進入：回傳 (done,total) 回呼。離開：乾淨收尾（即使區塊內發生例外）。"""
    p = _Progress(label, stream if stream is not None else sys.stderr, threshold)
    try:
        yield p.update
    finally:
        p.close()
