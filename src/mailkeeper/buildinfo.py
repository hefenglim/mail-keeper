"""建置資訊：build 時間戳（格式 ``YYYYMMDD-HHMMSS``）。

build 時間於建置時由 ``scripts/stamp-build.py`` 寫入同套件下的 ``_buildinfo.py``
（gitignored，僅存在於建置後的 wheel）。未經建置（dev / editable 安裝）時回退為
本模組檔案的修改時間，永遠回傳合理字串、絕不拋例外（憲法 Principle VI）。
"""
from __future__ import annotations

import os
from datetime import datetime

_FALLBACK = "unknown"


def build_stamp() -> str:
    """回傳 build 時間戳 ``YYYYMMDD-HHMMSS``；無 build 烙印則用套件檔案 mtime 回退。"""
    try:
        from ._buildinfo import BUILD  # type: ignore[import-not-found, import-untyped]

        if BUILD:
            return str(BUILD)
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(__file__)).strftime("%Y%m%d-%H%M%S")
    except Exception:
        return _FALLBACK
