"""build 時間戳：格式與來源優先序。"""
from __future__ import annotations

import re
import sys
import types

from mailkeeper import buildinfo


def test_build_stamp_format():
    s = buildinfo.build_stamp()
    assert re.fullmatch(r"\d{8}-\d{6}", s) or s == "unknown"  # YYYYMMDD-HHMMSS 或回退


def test_build_stamp_prefers_generated_buildinfo(monkeypatch):
    # 若 build 流程已寫入 _buildinfo.BUILD，build_stamp() 應採用之（而非 mtime 回退）
    mod = types.ModuleType("mailkeeper._buildinfo")
    mod.BUILD = "20260101-123456"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mailkeeper._buildinfo", mod)
    assert buildinfo.build_stamp() == "20260101-123456"
