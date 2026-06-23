"""於建置時把目前日期時間（YYYYMMDD-HHMMSS）寫入 src/mailkeeper/_buildinfo.py。

由建置/發版流程在 `python -m build` 之前呼叫，使 wheel 烙印真實 build 時間。
產生的 _buildinfo.py 為 gitignored（不提交、不進原始碼控管）。
"""
from __future__ import annotations

import datetime
import pathlib

stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
target = pathlib.Path(__file__).resolve().parent.parent / "src" / "mailkeeper" / "_buildinfo.py"
target.write_text(
    f'# Generated at build time by scripts/stamp-build.py. Do not edit, do not commit.\nBUILD = "{stamp}"\n',
    encoding="utf-8",
)
print(f"stamped build {stamp} -> {target}")
