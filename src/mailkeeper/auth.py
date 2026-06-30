"""OAuth2 認證模組。

Outlook.com 已停用 Basic Auth，IMAP 必須使用 XOAUTH2，
因此這裡負責透過 MSAL 取得並快取 OAuth2 access token。
採用 device code flow，不需要在本機跑 redirect 伺服器，最適合 CLI / 工具程式。
"""
from __future__ import annotations

import os

import msal  # type: ignore[import-untyped]  # msal 未提供型別 stub

from .config_store import Configuration
from .domain import ReauthRequired


def _load_cache(path: str) -> msal.SerializableTokenCache:  # pragma: no cover - MSAL/磁碟 I/O，由 release-smoke 實帳號驗證
    cache = msal.SerializableTokenCache()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: msal.SerializableTokenCache, path: str) -> None:  # pragma: no cover - MSAL/磁碟 I/O
    if cache.has_state_changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(cache.serialize())


def _username(app: msal.PublicClientApplication, result: dict) -> str:
    """從認證結果 / 快取帳號取出已認證的 email / 帳號名。"""
    claims = result.get("id_token_claims") or {}
    name = claims.get("preferred_username") or claims.get("email")
    if name:
        return str(name)
    accounts = app.get_accounts()
    return str(accounts[0]["username"]) if accounts else ""


def get_token_silent(cfg: Configuration) -> str:
    """僅靜默續期：以既有快取帳號的 refresh token 取得新的 access token。

    無快取帳號 / refresh token 失效或被撤銷 → 擲 :class:`ReauthRequired`；
    **絕不**退化為互動式 device flow（供 R7 重連時背景續期，不打斷使用者）。
    """
    cache = _load_cache(cfg.token_cache_path)
    app = msal.PublicClientApplication(
        cfg.client_id, authority=cfg.authority, token_cache=cache
    )
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(cfg.scopes, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache, cfg.token_cache_path)
            return str(result["access_token"])
    raise ReauthRequired("需重新登入：無法以既有授權靜默續期，請重新執行以登入。")


def get_access_token(cfg: Configuration) -> tuple[str, str]:
    """取得 (access token, 已認證帳號 email)。

    優先用快取的 refresh token 靜默更新，必要時才走 device code 互動登入。
    client_id / authority / scopes / 快取路徑皆來自 cfg。
    """
    cache = _load_cache(cfg.token_cache_path)
    app = msal.PublicClientApplication(
        cfg.client_id,
        authority=cfg.authority,
        token_cache=cache,
    )

    # 1) 嘗試靜默取得 (使用既有帳號的 refresh token)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(cfg.scopes, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache, cfg.token_cache_path)
            return result["access_token"], _username(app, result)

    # 2) 第一次或 token 失效 → device code flow
    flow = app.initiate_device_flow(scopes=cfg.scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"無法啟動 device flow：{flow.get('error_description')}")

    # 提示使用者開啟網址、輸入代碼完成登入。
    # 防卡死：device flow 至多等待到代碼有效期 (flow["expires_in"]) 結束即停止，
    # 不會無限卡住；上方的 silent refresh 路徑完全不受此影響。
    print(flow["message"])  # pragma: no cover - 互動：印出 device-flow 登入提示
    result = app.acquire_token_by_device_flow(flow)  # pragma: no cover - 互動：阻塞輪詢真實登入完成

    if "access_token" not in result:
        raise RuntimeError(
            f"取得 token 失敗：{result.get('error')} - {result.get('error_description')}"
        )
    _save_cache(cache, cfg.token_cache_path)
    return result["access_token"], _username(app, result)
