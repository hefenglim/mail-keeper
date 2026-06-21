"""跨平台、防崩潰的輸出層。

啟動時把 stdout/stderr 重設為 UTF-8，並以一個永不拋例外的安全寫入器包覆，
讓任何 ``print()`` / :func:`safe_print` 在非 UTF-8 主控台或被重導向時，
也不會因 ``UnicodeEncodeError`` 而崩潰。無法被輸出目標表示的字元，
以 ``backslashreplace`` 佔位（保留資訊、可除錯），而非靜默省略。
"""
from __future__ import annotations

import sys
from typing import IO, Any


class _SafeWriter:
    """包覆一個文字串流；``write()`` 永不因編碼失敗而拋例外。"""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.encoding = getattr(wrapped, "encoding", None) or "utf-8"

    def write(self, s: str) -> int:
        try:
            return self._wrapped.write(s)
        except UnicodeEncodeError:
            enc = self.encoding
            safe = s.encode(enc, "backslashreplace").decode(enc)
            return self._wrapped.write(safe)

    def flush(self) -> None:
        try:
            self._wrapped.flush()
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def setup() -> None:
    """重設 stdout/stderr 為 UTF-8 並套上安全寫入器。程式啟動時呼叫一次。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or isinstance(stream, _SafeWriter):
            continue  # 冪等：已包裹過就不重複包
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
        setattr(sys, name, _SafeWriter(stream))


def safe_print(
    *values: Any,
    sep: str = " ",
    end: str = "\n",
    file: IO[str] | None = None,
    flush: bool = False,
) -> None:
    """像 ``print()``，但對無法編碼的字元以 backslashreplace 降級、永不拋例外。"""
    target: Any = sys.stdout if file is None else file
    text = sep.join(str(v) for v in values) + end
    try:
        target.write(text)
    except UnicodeEncodeError:
        enc = getattr(target, "encoding", None) or "ascii"
        target.write(text.encode(enc, "backslashreplace").decode(enc))
    if flush:
        target.flush()
