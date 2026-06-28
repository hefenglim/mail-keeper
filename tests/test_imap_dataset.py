"""母版資料集完整性 + 雙層驗證示範（指令日誌 + 資料變動）—— 跑在線級 IMAP 引擎上。

（P3：已自 FakeIMAPConn 遷移至 imap_server 引擎；產品零改動，全程真 imaplib over 引擎。）
"""
from __future__ import annotations

from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_NEWSLETTER_UID,
    INBOX_SEEN_UID,
    INBOX_USER_DELETED_UID,
    fresh_server,
    master_mailboxes,
)
from imap_sim import DELETED, SEEN  # 模組級常數（母版用）
from imap_transport import connected_client

from mailkeeper import classifier, cli
from mailkeeper.csv_io import ClassificationRow


def _rows(*specs):
    return [ClassificationRow(*s) for s in specs]


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr("mailkeeper.imap_client.time.sleep", lambda s: None)


# ── 母版完整性 ──────────────────────────────────────────────────────────────

def test_master_covers_required_scenarios():
    mb = master_mailboxes()
    assert set(mb) == {"INBOX", "Sent", "Archive", "Work/Projects", "台北"}
    inbox = {m.uid: m for m in mb["INBOX"]}
    assert len(inbox) == 8
    assert SEEN in inbox[INBOX_SEEN_UID].flags
    assert DELETED in inbox[INBOX_USER_DELETED_UID].flags  # 使用者自標刪
    assert inbox[107].fields["SUBJECT"] == ""  # 空主旨
    assert len(inbox[108].fields["SUBJECT"]) == 200  # 超長主旨
    assert mb["Archive"] == []  # 空夾（搬移目標）


def test_fresh_datasets_are_independent_copies(monkeypatch):
    # 每次 fresh_server 從母版獨立深拷貝：在一個引擎動土，另一個與母版皆不受影響
    s1 = fresh_server()
    connected_client(monkeypatch, s1).move(str(INBOX_SEEN_UID), "Archive", "INBOX")  # 在 s1 搬走一封
    assert len(s1.mailboxes["INBOX"]) == 7
    assert len(fresh_server().mailboxes["INBOX"]) == 8       # 另一份不受 s1 影響
    assert len(master_mailboxes()["INBOX"]) == 8            # 母版本身也未被汙染


def test_client_lists_cjk_and_nested_folders(monkeypatch):
    folders = connected_client(monkeypatch, fresh_server()).list_folders()
    assert "台北" in folders and "Work/Projects" in folders


# ── 雙層驗證：搬移（UID MOVE 主路徑）───────────────────────────────────────

def test_two_layer_move_only_target_changes(monkeypatch):
    server = fresh_server()
    before = server.snapshot()

    connected_client(monkeypatch, server).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")

    # 第一層：指令動作日誌符合規格（可寫 SELECT + UID MOVE 該封）
    moves = server.commands("UID MOVE")
    assert len(moves) == 1 and moves[0].args == (str(INBOX_NEWSLETTER_UID), "Archive")
    assert server.commands("SELECT")[-1].response_code == "READ-WRITE"

    # 第二層：資料變動合理
    after = server.snapshot()
    assert {u for u, _ in before["INBOX"]} - {u for u, _ in after["INBOX"]} == {INBOX_NEWSLETTER_UID}
    assert len(after["Archive"]) == 1  # Archive 多一封（新 UID）
    for box in ("Sent", "Work/Projects", "台北"):
        assert before[box] == after[box]  # 其他信箱完全沒動
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in after["INBOX"]  # 使用者自標刪原封不動


# ── 雙層驗證：fallback（無 UID MOVE）不誤刪他人 \Deleted ────────────────────

def test_two_layer_fallback_preserves_user_deleted(monkeypatch):
    server = fresh_server(supports_move=False)
    before_inbox = {u for u, _ in server.snapshot()["INBOX"]}

    connected_client(monkeypatch, server).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")

    # 第一層：fallback 安全序列 copy → store(+\Deleted) → UID EXPUNGE（非整夾 expunge）
    assert server.command_count("UID COPY") == 1 and server.command_count("UID EXPUNGE") == 1
    assert server.command_count("EXPUNGE") == 0  # 沒有走整夾 EXPUNGE
    store = server.commands("UID STORE")[0]
    assert store.args[1] == "+FLAGS" and "Deleted" in store.args[2]

    # 第二層：目標已搬走、使用者自標刪的 106 仍在（未被誤清）
    after_inbox = {u for u, _ in server.snapshot()["INBOX"]}
    assert INBOX_NEWSLETTER_UID not in after_inbox
    assert INBOX_USER_DELETED_UID in after_inbox
    assert before_inbox - after_inbox == {INBOX_NEWSLETTER_UID}
    assert len(server.mailboxes["Archive"]) == 1


# ── US1：token 中途過期 → 分類仍全完成（雙層）──────────────────────────────

def test_classify_completes_through_token_expiry(monkeypatch):
    server = fresh_server()
    _no_sleep(monkeypatch)
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    server.arm_expiry(before_op="move", nth=1)  # 批次搬移時 token 過期（EOF）→ 透明重連續完
    rows = _rows(
        (str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"),
        (str(INBOX_CJK_UID), "INBOX", "Archive"),
        ("107", "INBOX", "Archive"),
    )
    cache = classifier.ClassifyCache()
    items = classifier.build_report(client, rows, cache=cache)
    results = classifier.execute(client, items, cache=cache)
    # 第一層：全部成功（透明恢復），日誌有重連（≥2 次認證）
    assert len(results) == 3 and all(r.ok for r in results)
    assert server.command_count("AUTHENTICATE") >= 2
    # 第二層：3 封都進 Archive，他人 \Deleted(106) 未波及
    assert len(server.mailboxes["Archive"]) == 3
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in server.snapshot()["INBOX"]


# ── US2：效率（用指令日誌抓冗餘）—— 同一流程整夾只讀一次、list 只一次 ──────

def test_classify_reads_each_source_folder_once_via_log(monkeypatch):
    server = fresh_server()
    client = connected_client(monkeypatch, server)
    cache = classifier.ClassifyCache()
    rows = _rows(
        (str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"),
        (str(INBOX_CJK_UID), "INBOX", "Archive"),
    )
    items = classifier.build_report(client, rows, cache=cache)
    classifier.execute(client, items, cache=cache)
    # 指令日誌效率斷言（P1）：存在性改 UID SEARCH——INBOX 現存查詢只一次、零整夾標頭 FETCH
    assert server.command_count("UID FETCH") == 0       # 不再抓整夾標頭
    assert server.command_count("UID SEARCH") == 1      # INBOX 現存查詢只一次（報告讀、執行重用、不二次掃描）
    assert server.fetch_count("INBOX") == 0
    # 資料夾清單只讀一次（report/new_folders/execute 共用快取）
    assert server.command_count("LIST") == 1


# ── US1/FR-011：dry-run 不被繞過（即使底層會重連）──────────────────────────

def test_dry_run_moves_nothing_even_with_reconnect(monkeypatch, tmp_path):
    server = fresh_server()
    client = connected_client(monkeypatch, server, token_provider=lambda: "tok")
    before = server.snapshot()
    p = tmp_path / "w.csv"
    p.write_text(
        f"uid,current_folder,target_folder\n{INBOX_NEWSLETTER_UID},INBOX,Archive\n",
        encoding="utf-8-sig",
    )
    cli.classify(client, str(p), run=False, interactive=False)  # 未確認 → 不搬
    assert server.snapshot() == before  # 狀態完全不變（dry-run）
    destructive = [op for op in server.log if op.command in ("UID MOVE", "UID COPY", "UID EXPUNGE")]
    assert not destructive and server.command_count("EXPUNGE") == 0  # 無任何破壞性動作
