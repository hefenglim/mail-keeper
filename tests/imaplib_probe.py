"""以記憶體腳本驅動**真正的 imaplib** 命令/解析迴圈（不碰真實 socket）。

用途：把符合 RFC 3501 的原始 wire bytes 餵進產品實際使用的 imaplib 解析器，取得它解析出的
`(typ, data)` 結構，作為「真實規格」的權威基準，去對拍 FakeIMAPConn 的輸出（見 test_imap_fidelity）。
這是「不確定規格就拿真的跑一次確認」的離線等價。
"""
from __future__ import annotations

import imaplib
from typing import Optional


class ScriptedIMAP4(imaplib.IMAP4):
    """傳輸層改由記憶體腳本供給；其餘走真實 imaplib（指令組裝、literal 讀取、回應解析）。

    ``responses``：關鍵字（如 ``"FETCH"`` / ``"LIST"`` / ``"SEARCH"``）→ 該指令的 untagged 回應 bytes
    （不含結尾的 tagged ``<tag> OK``，由本類別自動補上）。
    """

    def __init__(self, responses: dict[str, bytes]) -> None:
        self._responses = responses
        self._inbuf = bytearray()
        self._cmdbuf = bytearray()
        super().__init__(host="localhost", port=143)

    # --- 傳輸層覆寫（imaplib 命令迴圈只透過這幾個方法做 IO）---
    def open(self, host: str = "", port: int = 0, timeout: Optional[float] = None) -> None:
        self.host, self.port = host, port
        self._inbuf += b"* OK [CAPABILITY IMAP4rev1 UIDPLUS MOVE] ready\r\n"

    def read(self, size: int) -> bytes:
        data = bytes(self._inbuf[:size])
        del self._inbuf[:size]
        return data

    def readline(self) -> bytes:
        idx = self._inbuf.find(b"\r\n")
        if idx == -1:
            line = bytes(self._inbuf)
            self._inbuf.clear()
            return line
        idx += 2
        line = bytes(self._inbuf[:idx])
        del self._inbuf[:idx]
        return line

    def send(self, data: bytes) -> None:
        self._cmdbuf += data
        if self._cmdbuf.endswith(b"\r\n"):
            cmd = bytes(self._cmdbuf)
            self._cmdbuf.clear()
            tag = cmd.split(b" ", 1)[0]
            up = cmd.upper()
            if b"LOGOUT" in up:
                self._inbuf += b"* BYE bye\r\n" + tag + b" OK LOGOUT completed\r\n"
                return
            body = b""
            for key, resp in self._responses.items():
                if key.encode() in up:
                    body = resp
                    break
            self._inbuf += body + tag + b" OK completed\r\n"

    def shutdown(self) -> None:  # 無真實 socket
        pass


def real_uid_fetch(wire: bytes, uidset: str, items: str) -> tuple:
    """以真 imaplib 解析一段 FETCH wire bytes，回傳 (typ, data)。"""
    m = ScriptedIMAP4({"FETCH": wire})
    m.state = "SELECTED"
    return m.uid("FETCH", uidset, items)


def real_uid_search(wire: bytes) -> tuple:
    m = ScriptedIMAP4({"SEARCH": wire})
    m.state = "SELECTED"
    return m.uid("SEARCH", None, "ALL")


def real_list(wire: bytes) -> tuple:
    m = ScriptedIMAP4({"LIST": wire})
    m.state = "AUTH"
    return m.list()
