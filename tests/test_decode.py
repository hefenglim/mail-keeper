"""US2 — resilient worldwide header decoding. Written test-first."""
from __future__ import annotations

import base64

import pytest

from mailkeeper.imap_client import _decode


def ew(text: str, charset: str) -> str:
    """Build a base64 MIME encoded-word."""
    b = base64.b64encode(text.encode(charset)).decode("ascii")
    return f"=?{charset}?B?{b}?="


@pytest.mark.parametrize(
    "text,charset",
    [
        ("新年快樂", "utf-8"),
        ("中文測試", "big5"),
        ("简体中文", "gbk"),
        ("简体", "gb2312"),
        ("日本語テスト", "iso-2022-jp"),
        ("한국어", "euc-kr"),
    ],
)
def test_decode_standard_encoded_words(text, charset):
    assert _decode(ew(text, charset)) == text


def test_decode_folded_multisegment():
    folded = f"{ew('新年', 'big5')}\n {ew('快樂', 'big5')}"
    out = _decode(folded)
    assert "新年" in out and "快樂" in out
    assert "=?" not in out  # no raw encoded-word leaks


def test_decode_strips_leading_fold_whitespace():
    # backlog C3：不合規折行使值落續行，email 攤平後可能殘留前導折疊空白（3.10 保留、3.12 已去）。
    # _decode 須去除前導折疊空白 → 輸出版本無關；內部折疊空白仍正規化為單一空白。
    assert _decode(" Quarterly Report") == "Quarterly Report"        # 前導空白（無換行）→ 去除
    assert _decode("\r\n Quarterly Report") == "Quarterly Report"    # 含換行續行 → 去除
    assert _decode("Hello\r\n World") == "Hello World"               # 內部折疊空白 → 單一空白（保留）


def test_decode_unknown_charset_recovers_via_detection():
    b = base64.b64encode("测试".encode("utf-8")).decode("ascii")
    val = f"=?x-unknown-charset?B?{b}?="
    assert _decode(val) == "测试"


def test_decode_malformed_bytes_never_raises():
    out = _decode("=?utf-8?B?////?=")  # decodes to invalid utf-8 bytes
    assert isinstance(out, str)  # best-effort, no exception


def test_decode_mixed_ascii_and_encoded_word():
    out = _decode(f"Re: {ew('主題', 'utf-8')}")
    assert "Re:" in out and "主題" in out


@pytest.mark.parametrize(
    "value,expected",
    [(None, ""), ("", ""), ("Plain ASCII", "Plain ASCII")],
)
def test_decode_edge_values(value, expected):
    assert _decode(value) == expected
