"""config.json 載入、首次引導與驗證。

使用者專屬設定（``client_id``、``email``，及可選 IMAP host/port/timeout）放在
**執行工作目錄**下的 ``config.json``，與 ``token_cache.bin`` 同目錄。認證關鍵設定
（authority、scopes）鎖在程式碼，不從 json 讀。以 ``_`` 開頭的欄位僅為說明、會被忽略。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config

CONFIG_FILENAME = "config.json"
CLIENT_ID_PLACEHOLDER = "YOUR-AZURE-APP-CLIENT-ID"
EMAIL_PLACEHOLDER = "your-name@outlook.com"
HELP_URL = "https://github.com/your-org/mailkeeper#setup"


class ConfigError(RuntimeError):
    """設定相關錯誤；由 cli 的錯誤邊界轉成乾淨訊息（不噴 traceback）。"""


class ConfigNotFound(ConfigError):
    """工作目錄下找不到 config.json（首次執行）。"""


@dataclass(frozen=True)
class Configuration:
    """一次執行的有效設定。"""

    client_id: str
    email: str
    imap_host: str
    imap_port: int
    timeout: float
    authority: str
    scopes: list[str]
    token_cache_path: str
    # R7 韌性設定（可選；無效退安全預設、不崩潰）。具預設以維持直接建構的相容性。
    max_consecutive_failures: int = config.MAX_CONSECUTIVE_FAILURES
    max_reconnect_attempts: int = config.MAX_RECONNECT_ATTEMPTS
    max_retries_per_op: int = config.MAX_RETRIES_PER_OP
    backoff_base_seconds: float = config.BACKOFF_BASE_SECONDS
    backoff_cap_seconds: float = config.BACKOFF_CAP_SECONDS
    # feature 008：每批 FETCH 標頭封數（可選；無效/缺漏退預設 50、下限 1）。
    fetch_batch_size: int = config.FETCH_BATCH_DEFAULT


def config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / CONFIG_FILENAME


def _template() -> dict[str, Any]:
    return {
        "_README": (
            "MailKeeper 設定：填入下方 client_id 與 email 後重新執行。"
            " 以 _ 開頭的欄位僅為說明、會被忽略。"
        ),
        "_help_url": HELP_URL,
        "client_id": CLIENT_ID_PLACEHOLDER,
        "email": EMAIL_PLACEHOLDER,
        "imap_host": config.IMAP_HOST,
        "imap_port": config.IMAP_PORT,
        "timeout": config.IMAP_TIMEOUT,
    }


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    """以暫存檔 + os.replace 原子寫入，避免寫到一半毀檔。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def bootstrap(cwd: Path | None = None) -> Path:
    """產生範本 config.json 並回傳路徑。呼叫端負責印指示並以非零碼結束。"""
    path = config_path(cwd)
    _atomic_write(path, _template())
    return path


def _require(value: str, field: str, placeholder: str, path: Path) -> str:
    if not value or value == placeholder:
        raise ConfigError(
            f"設定檔 {path} 的必填欄位 '{field}' 尚未填寫，請編輯後重試。"
        )
    return value


def _as_int(value: Any, default: int, field: str, path: Path) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"設定檔 {path} 的 '{field}' 必須是整數。") from None


def _as_float(value: Any, default: float, field: str, path: Path) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"設定檔 {path} 的 '{field}' 必須是數字。") from None


def _as_positive_int(value: Any, default: int) -> int:
    """正整數否則退預設（韌性設定：無效永不崩潰，FR-008）。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _as_positive_float(value: Any, default: float) -> float:
    """正數否則退預設。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f > 0 else default


def load(cwd: Path | None = None) -> Configuration:
    """讀取並驗證 config.json。缺檔→ConfigNotFound；未填/壞檔→ConfigError。"""
    path = config_path(cwd)
    if not path.exists():
        raise ConfigNotFound(str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"無法解析設定檔 {path}：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"設定檔 {path} 格式錯誤（應為 JSON 物件）。")

    client_id = _require(
        str(data.get("client_id", "")).strip(), "client_id", CLIENT_ID_PLACEHOLDER, path
    )
    email = _require(
        str(data.get("email", "")).strip(), "email", EMAIL_PLACEHOLDER, path
    )
    imap_host = str(data.get("imap_host") or config.IMAP_HOST)
    imap_port = _as_int(data.get("imap_port"), config.IMAP_PORT, "imap_port", path)
    timeout = _as_positive_float(data.get("timeout"), config.IMAP_TIMEOUT)  # F9：0/負/無效 → 退預設

    # R7 韌性設定：無效/缺漏一律退安全預設（永不崩潰）。封頂須 ≥ base。
    backoff_base = _as_positive_float(data.get("backoff_base_seconds"), config.BACKOFF_BASE_SECONDS)
    backoff_cap = _as_positive_float(data.get("backoff_cap_seconds"), config.BACKOFF_CAP_SECONDS)
    if backoff_cap < backoff_base:
        backoff_cap = config.BACKOFF_CAP_SECONDS if config.BACKOFF_CAP_SECONDS >= backoff_base else backoff_base

    return Configuration(
        client_id=client_id,
        email=email,
        imap_host=imap_host,
        imap_port=imap_port,
        timeout=timeout,
        authority=config.AUTHORITY,
        scopes=list(config.SCOPES),
        token_cache_path=config.TOKEN_CACHE_PATH,
        max_consecutive_failures=_as_positive_int(
            data.get("max_consecutive_failures"), config.MAX_CONSECUTIVE_FAILURES
        ),
        max_reconnect_attempts=_as_positive_int(
            data.get("max_reconnect_attempts"), config.MAX_RECONNECT_ATTEMPTS
        ),
        max_retries_per_op=_as_positive_int(
            data.get("max_retries_per_op"), config.MAX_RETRIES_PER_OP
        ),
        backoff_base_seconds=backoff_base,
        backoff_cap_seconds=backoff_cap,
        fetch_batch_size=_as_positive_int(
            data.get("fetch_batch_size"), config.FETCH_BATCH_DEFAULT
        ),
    )


def write_email(new_email: str, cwd: Path | None = None) -> None:
    """只更新 config.json 的 email 欄、原子寫入（其餘欄位原樣保留）。"""
    path = config_path(cwd)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["email"] = new_email
    _atomic_write(path, data)
