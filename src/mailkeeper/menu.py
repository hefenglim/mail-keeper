"""互動選單：顯示功能清單、讀取選擇、路由到對應動作。

與 cli 解耦 —— 接受 ``(label, callable)`` 清單；讀取與輸出皆可注入以利離線測試。
"""
from __future__ import annotations

from typing import Callable

from . import console

Option = tuple[str, Callable[[], None]]


def run(
    options: list[Option],
    *,
    read: Callable[[str], str] = input,
    out: Callable[..., None] | None = None,
) -> None:
    emit = out if out is not None else console.safe_print
    while True:
        emit("\n=== MailKeeper 選單 ===")
        for i, (label, _) in enumerate(options, 1):
            emit(f"  {i}. {label}")
        emit("  0. 離開")
        try:
            choice = read("請輸入選項編號：").strip()
        except EOFError:
            return
        if choice in ("0", "", "q", "quit"):
            return
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            options[int(choice) - 1][1]()
        else:
            emit("無效選項，請重試。")
