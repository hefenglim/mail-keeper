"""MailKeeper CLI —— 啟動選單 / 子指令：匯出工作表、匯出資料夾清單、依 CSV 分類。"""
from __future__ import annotations

import argparse
import contextlib
import sys
from typing import Callable, Iterator

from . import __version__, buildinfo, classifier, config_store, console, csv_io, menu, progress
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


@contextlib.contextmanager
def _connect() -> Iterator[MailBackend]:
    """載入設定 → 缺檔則 bootstrap 並結束 → 認證 → 帳號確認 → 連線，yield 後端。"""
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
        yield client


def _run() -> None:
    """相容保留（feature 001/002）：載入設定 → 認證 → 列出並整理。"""
    with _connect() as client:
        run_listing(client, build_rules(), dry_run=True)


# ---------- 三個 CSV 功能（皆接受注入式 backend，便於離線測試） ----------

def export_worksheet(backend: MailBackend, folder: str, out: str) -> None:
    out = csv_io.ensure_csv_suffix(out)
    with progress.reporter(f"讀取「{folder}」標頭") as on_progress:
        headers = backend.list_headers(folder, on_progress=on_progress)
    csv_io.write_worksheet(headers, folder, out)
    console.safe_print(f"已將資料夾「{folder}」的 {len(headers)} 封郵件匯出到 {out}")


def export_folders(backend: MailBackend, out: str) -> None:
    out = csv_io.ensure_csv_suffix(out)
    folders = backend.list_folders()
    csv_io.write_folders(folders, out)
    console.safe_print(f"已匯出 {len(folders)} 個資料夾到 {out}")


def _print_report(items: list[classifier.ReportItem], to_create: list[str]) -> None:
    moves = [it for it in items if it.status == classifier.CANDIDATE]
    skips = [it for it in items if it.status == classifier.SKIP]
    infes = [it for it in items if it.status == classifier.INFEASIBLE]
    console.safe_print(
        f"檢查報告：將搬移 {len(moves)}、無變動 {len(skips)}、不可行 {len(infes)}"
    )
    if to_create:
        console.safe_print(f"⚠ 將新建 {len(to_create)} 個資料夾：{', '.join(to_create)}")
    for it in moves:
        console.safe_print(
            f"  將搬移：{it.row.uid}@{it.row.current_folder} → {it.row.target_folder}"
        )
    for it in infes:
        console.safe_print(
            f"  不可行：{it.row.uid}@{it.row.current_folder} → {it.row.target_folder}（{it.reason}）",
            file=sys.stderr,
        )


def _prompt_yes_no() -> bool:
    return input("確認執行搬移？(y/N)：").strip().lower() in ("y", "yes")


def classify(
    backend: MailBackend,
    in_path: str,
    *,
    run: bool,
    interactive: bool,
    ask: Callable[[], bool] | None = None,
) -> None:
    in_path = csv_io.ensure_csv_suffix(in_path)
    rows = csv_io.read_worksheet(in_path)
    items = classifier.build_report(backend, rows, progress=progress.reporter)
    _print_report(items, classifier.new_folders(backend, items))
    if not classifier.candidates(items):
        console.safe_print("沒有需要搬移的列（無變動或皆不可行）。")
        return

    proceed = run
    if not proceed and interactive:
        proceed = (ask or _prompt_yes_no)()
    if not proceed:
        console.safe_print("（預覽）未搬移。確認無誤後加 --run，或於互動中輸入 y 執行。")
        return

    with progress.reporter("搬移分類") as on_progress:
        results = classifier.execute(
            backend, items, on_progress=on_progress, progress=progress.reporter
        )
    ok = sum(1 for r in results if r.ok)
    console.safe_print(f"完成：成功搬移 {ok} / {len(results)}。")
    remaining = len(classifier.candidates(items)) - len(results)
    if remaining > 0:
        console.safe_print(
            f"⚠ 偵測到連續失敗、疑似連線中斷，已提前停止；剩餘 {remaining} 筆未處理，"
            "請稍後重試（必要時重新登入）。",
            file=sys.stderr,
        )
    for r in results:
        if not r.ok:
            console.safe_print(
                f"  失敗：{r.row.uid}@{r.row.current_folder} → {r.row.target_folder}：{r.error}",
                file=sys.stderr,
            )


# ---------- 互動選單 ----------

def _menu_export_worksheet(backend: MailBackend) -> None:
    folders = backend.list_folders()
    for i, name in enumerate(folders, 1):
        console.safe_print(f"  {i}. {name}")
    sel = input("選擇要匯出的資料夾編號：").strip()
    if not (sel.isdigit() and 1 <= int(sel) <= len(folders)):
        console.safe_print("無效的資料夾選擇。", file=sys.stderr)
        return
    folder = folders[int(sel) - 1]
    out = input("輸出 CSV 路徑 [worksheet.csv]：").strip() or "worksheet.csv"
    export_worksheet(backend, folder, out)


def _menu_export_folders(backend: MailBackend) -> None:
    out = input("輸出 CSV 路徑 [folders.csv]：").strip() or "folders.csv"
    export_folders(backend, out)


def _menu_classify(backend: MailBackend) -> None:
    in_path = input("工作表 CSV 路徑 [worksheet.csv]：").strip() or "worksheet.csv"
    classify(backend, in_path, run=False, interactive=True)


def _menu_header() -> str:
    """主選單開頭：應用名稱 + 版本號 + build 日期時間（YYYYMMDD-HHMMSS）。"""
    return f"=== MailKeeper v{__version__}｜build {buildinfo.build_stamp()} ==="


def _menu_options(backend: MailBackend) -> list[tuple[str, Callable[[], None]]]:
    return [
        ("匯出資料夾的所有電子郵件列表（功能1）", lambda: _menu_export_worksheet(backend)),
        ("匯出資料夾清單（功能2）", lambda: _menu_export_folders(backend)),
        ("依工作表分類（功能3）", lambda: _menu_classify(backend)),
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mailkeeper", description="Outlook 郵件 CSV 匯出/分類")
    sub = parser.add_subparsers(dest="command")

    p1 = sub.add_parser("export-worksheet", help="匯出某資料夾的分類工作表 CSV")
    p1.add_argument("--folder", required=True, help="來源資料夾")
    p1.add_argument("--out", default="worksheet.csv", help="輸出路徑")

    p2 = sub.add_parser("export-folders", help="匯出所有資料夾清單 CSV")
    p2.add_argument("--out", default="folders.csv", help="輸出路徑")

    p3 = sub.add_parser("classify", help="依工作表 CSV 檢查並搬移")
    p3.add_argument("--in", dest="in_path", required=True, help="工作表 CSV 路徑")
    p3.add_argument("--run", action="store_true", help="確認後實際搬移（預設只做檢查報告）")
    return parser


def main(argv: list[str] | None = None) -> None:
    console.setup()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "export-worksheet":
            with _connect() as backend:
                export_worksheet(backend, args.folder, args.out)
        elif args.command == "export-folders":
            with _connect() as backend:
                export_folders(backend, args.out)
        elif args.command == "classify":
            interactive = sys.stdin.isatty() and sys.stdout.isatty()
            with _connect() as backend:
                classify(backend, args.in_path, run=args.run, interactive=interactive)
        else:  # 無子指令
            if sys.stdin.isatty() and sys.stdout.isatty():
                with _connect() as backend:
                    menu.run(_menu_options(backend), header=_menu_header())
            else:
                parser.print_help(sys.stderr)
                raise SystemExit(2)
    except (RuntimeError, BackendError, OSError) as exc:
        # 已知失敗 (認證/IMAP/網路/逾時/設定/CSV)：乾淨訊息 + 非零碼，不噴 traceback。
        console.safe_print(f"MailKeeper 無法完成：{exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:  # 未預期錯誤：不噴 traceback、不外洩內容 (含 token)
        console.safe_print(
            f"MailKeeper 發生未預期錯誤（{type(exc).__name__}）。", file=sys.stderr
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
