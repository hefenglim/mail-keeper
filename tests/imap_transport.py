"""SimIMAP4_SSL —— 真 ``imaplib.IMAP4_SSL`` 子類，只覆寫「傳輸六法」接到 ImapServer 引擎。

關鍵：產品端建立的是**真正的** ``imaplib.IMAP4_SSL``（經 ``install_server`` 工廠換成本子類），
我們只把 socket 位置替換成記憶體伺服器——其餘命令組裝、literal 讀取、狀態機、錯誤包裝、
CAPABILITY 交握、AUTHENTICATE 續傳**全由真 imaplib 執行**。因此：
  * 上層產品程式（``imap_client``/``classifier``/``cli``）**零改動**即可在引擎上跑。
  * 保真度自動且完整——凡 imaplib 解析得出的 ``(typ, data)`` 即為「真實規格」，無臆造空間。

被覆寫的六個方法（imaplib 命令迴圈只透過這些做 IO，見 CPython ``imaplib`` 原始碼）：
``open`` / ``_create_socket``（不開真 socket）、``send``（→ ``server.feed()``）、
``read`` / ``readline``（從引擎回應緩衝吐 bytes）、``shutdown``（no-op）。
"""
from __future__ import annotations

import imaplib
from typing import Any, Optional

from imap_server import ImapServer


class SimIMAP4_SSL(imaplib.IMAP4_SSL):
    """傳輸層改由記憶體引擎供給；其餘全繼承真 imaplib。"""

    def __init__(self, server: ImapServer, host: str = "", port: int = 993, timeout: Optional[float] = None) -> None:
        self._server = server
        self._inbuf = bytearray()   # 伺服器 → 用戶端（imaplib read/readline 由此取）
        self._outbuf = bytearray()  # 用戶端 → 伺服器（imaplib send 累積至完整行才派送）
        # 略過 IMAP4_SSL.__init__ 的 ssl_context 設定（_create_socket 已覆寫、不需真 SSL），
        # 直接走 IMAP4.__init__ → open() + _connect()（讀招呼、送 CAPABILITY）。
        imaplib.IMAP4.__init__(self, host, port, timeout)

    # --- 傳輸六法覆寫 ---
    def open(self, host: str = "", port: Optional[int] = 993, timeout: Optional[float] = None) -> None:
        self.host = host
        self.port = port if port is not None else 993
        self._inbuf += self._server.greeting()  # imaplib `_connect` 會立即讀此招呼

    def _create_socket(self, timeout: Any = None) -> Any:  # 永不被呼叫（open 已覆寫、不建 socket）
        return None

    def read(self, size: int) -> bytes:
        self._server.raise_if_socket_dead()  # 失效注入：oserror/sslerror 模式於讀取時拋（擬真斷線）
        data = bytes(self._inbuf[:size])
        del self._inbuf[:size]
        return data

    def readline(self) -> bytes:
        self._server.raise_if_socket_dead()  # 同上；eof 模式則由空緩衝自然 EOF（→ imaplib abort）
        idx = self._inbuf.find(b"\r\n")
        if idx == -1:  # 引擎一律回完整 CRLF 行；走到這代表緩衝已空 → imaplib 視為 EOF/abort
            line = bytes(self._inbuf)
            self._inbuf.clear()
            return line
        idx += 2
        line = bytes(self._inbuf[:idx])
        del self._inbuf[:idx]
        return line

    def send(self, data: Any) -> None:  # supertype 接受 Buffer；此處實際收到 imaplib 送出的 bytes
        # imaplib 可能分段送（命令列、AUTHENTICATE 的 base64 與 CRLF 各一次）；累積到完整行才派送。
        self._outbuf += data
        while True:
            idx = self._outbuf.find(b"\r\n")
            if idx == -1:
                break
            line = bytes(self._outbuf[:idx])  # 去掉 CRLF 的命令列
            del self._outbuf[: idx + 2]
            self._inbuf += self._server.feed(line)

    def shutdown(self) -> None:  # 無真實 socket
        pass


def install_server(monkeypatch: Any, server: ImapServer, *, capture: Optional[dict] = None) -> dict:
    """把 ``imaplib.IMAP4_SSL`` 換成「回傳綁定此引擎的 SimIMAP4_SSL」的工廠。

    讓**真實的** ``OutlookIMAPClient.connect()`` 跑在引擎之上；建構參數（host/port/timeout）
    記到回傳 dict，供逾時/連線測試查核。這是「所有 Outlook IMAP 連線在測試中改走線級引擎」的統一接點。
    """
    cap: dict = capture if capture is not None else {}
    cap.setdefault("constructed", 0)

    def factory(host: Any, port: Any, timeout: Any = None) -> SimIMAP4_SSL:
        cap["host"] = host
        cap["port"] = port
        cap["timeout"] = timeout
        cap["constructed"] += 1
        return SimIMAP4_SSL(server, host, port, timeout)

    monkeypatch.setattr("mailkeeper.imap_client.imaplib.IMAP4_SSL", factory)
    return cap


def connected_client(monkeypatch: Any, server: ImapServer, **client_kw: Any) -> Any:
    """install_server() + 建構真實 ``OutlookIMAPClient`` + connect()，回傳已連線 client。

    用於需要「重連」的測試：``_with_reconnect`` 重建 ``IMAP4_SSL`` 時，工廠讓它取回**同一個**引擎
    （於是 ``authenticate`` 再次被呼叫、session 恢復）。``client_kw`` 透傳如 token_provider/on_status。
    """
    from mailkeeper.imap_client import OutlookIMAPClient

    install_server(monkeypatch, server)
    c = OutlookIMAPClient("user@x.com", "tok", **client_kw)
    c.connect()
    return c
