"""MailKeeper CLI —— 進入點：登入 → 列出收件匣標題 → 套用整理規則。"""
from __future__ import annotations

import sys
from typing import Callable

from . import config_store, console
from .auth import get_access_token
from .imap_client import BackendError, OutlookIMAPClient
from .organizer import MailBackend, MailOrganizer, Rule, from_contains, subject_contains


def build_rules() -> list[Rule]:
    """在這裡定義你的整理規則 —— 日後調整整理需求改這裡即可。"""
    return [
        Rule(
            name="電子報歸檔",
            match=subject_contains("newsletter"),
            dest_folder="Newsletters",
            mark_read=True,
        ),
        Rule(
            name="重要寄件者加旗標",
            match=from_contains("boss@company.com"),
            flag=True,
        ),
    ]


def run_listing(backend: MailBackend, rules: list[Rule], *, dry_run: bool = True) -> None:
    """列出收件匣標題並套用整理規則。輸出全程走 console 安全寫入，不會因編碼崩潰。"""
    headers = backend.list_inbox_headers()
    console.safe_print(f"=== 收件匣 ({len(headers)} 封) ===")
    for h in headers:
        console.safe_print(f"{h.date} | {h.sender} | {h.subject}")

    # 套用整理規則 (預設 dry_run=True，確認無誤後改 False 才會真的動作)
    organizer = MailOrganizer(backend, rules)
    organizer.run(dry_run=dry_run)


# 帳號不一致時互動提問的選項代碼
CHOICE_USE_WRITE = "1"  # 用登入帳號並更新 config.json
CHOICE_USE_ONCE = "2"   # 用登入帳號（僅本次）
CHOICE_KEEP = "3"       # 保留設定的 email
CHOICE_ABORT = "4"      # 中止


def verify_account(
    configured_email: str,
    authenticated_email: str,
    *,
    interactive: bool,
    ask: Callable[[], str],
    write_back: Callable[[str], None],
) -> str:
    """比對設定 email 與實際登入帳號，回傳本次連線要用的 email。

    一致→直接用設定值；不一致且非互動→安全中止；不一致且可互動→提問四選項。
    """
    if configured_email.strip().lower() == authenticated_email.strip().lower():
        return configured_email

    if not interactive:
        raise config_store.ConfigError(
            f"登入帳號（{authenticated_email}）與設定的 email（{configured_email}）不一致，"
            "且目前為非互動模式，已安全中止。請修正 config.json 的 email，或改用正確帳號登入。"
        )

    console.safe_print(
        "MailKeeper 發現一個需要你協助確認的問題：\n"
        f"  設定的 email ：{configured_email}\n"
        f"  實際登入帳號：{authenticated_email}\n"
        "請選擇： [1] 用登入帳號並更新 config.json   [2] 用登入帳號（僅本次）"
        "   [3] 保留設定的 email   [4] 中止",
        file=sys.stderr,
    )
    choice = ask()
    if choice == CHOICE_USE_WRITE:
        write_back(authenticated_email)
        return authenticated_email
    if choice == CHOICE_USE_ONCE:
        return authenticated_email
    if choice == CHOICE_KEEP:
        return configured_email
    raise config_store.ConfigError("使用者選擇中止：請修正設定後重試。")


def _prompt_choice() -> str:
    return input("請輸入選項編號 [1-4]：").strip()


def _run() -> None:
    """實際流程：載入設定 → 認證 → 帳號確認 → 列出並整理。可被測試替換 / 注入失敗。"""
    try:
        cfg = config_store.load()
    except config_store.ConfigNotFound:
        path = config_store.bootstrap()
        console.safe_print(
            f"首次設定：已為你在 {path} 產生 config.json 範本。\n"
            "請填入 client_id 與 email（client_id 於 Azure / Entra 應用程式註冊取得），再重新執行。",
            file=sys.stderr,
        )
        raise SystemExit(2)

    token, authenticated_email = get_access_token(cfg)
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    email = verify_account(
        cfg.email,
        authenticated_email,
        interactive=interactive,
        ask=_prompt_choice,
        write_back=config_store.write_email,
    )
    with OutlookIMAPClient(
        email, token, host=cfg.imap_host, port=cfg.imap_port, timeout=cfg.timeout
    ) as client:
        run_listing(client, build_rules(), dry_run=True)


def main() -> None:
    console.setup()
    try:
        _run()
    except (RuntimeError, BackendError, OSError) as exc:
        # 已知失敗 (認證/IMAP/網路/逾時/設定)：乾淨訊息 + 非零碼，不噴 traceback。
        console.safe_print(f"MailKeeper 無法完成：{exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # 未預期錯誤：不噴 traceback、不外洩內容 (含 token)
        console.safe_print(
            f"MailKeeper 發生未預期錯誤（{type(exc).__name__}）。", file=sys.stderr
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
