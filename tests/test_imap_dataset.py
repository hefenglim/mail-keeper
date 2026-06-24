"""母版資料集完整性 + 雙層驗證示範（指令日誌 + 資料變動）。"""
from __future__ import annotations

from imap_dataset import (
    INBOX_CJK_UID,
    INBOX_NEWSLETTER_UID,
    INBOX_SEEN_UID,
    INBOX_USER_DELETED_UID,
    fresh_sim,
    master_mailboxes,
)
from imap_sim import DELETED, SEEN, client_on, connected_client

from mailkeeper import classifier, cli
from mailkeeper.csv_io import ClassificationRow


def _rows(*specs):
    return [ClassificationRow(*s) for s in specs]


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


def test_fresh_sims_are_independent_copies():
    s1 = fresh_sim()
    s1.select("INBOX")
    s1.uid("store", str(INBOX_SEEN_UID), "+FLAGS", "(\\Deleted)")
    s1.expunge()  # 在 s1 動土
    s2 = fresh_sim()
    # s2 完全不受 s1 影響（從母版獨立複製）
    assert len(s2.mailboxes["INBOX"]) == 8
    # 母版本身也未被汙染
    assert len(master_mailboxes()["INBOX"]) == 8


def test_client_lists_cjk_and_nested_folders():
    folders = client_on(fresh_sim()).list_folders()
    assert "台北" in folders and "Work/Projects" in folders


# ── 雙層驗證：搬移（UID MOVE 主路徑）───────────────────────────────────────

def test_two_layer_move_only_target_changes():
    sim = fresh_sim()
    before = sim.snapshot()

    client_on(sim).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")

    # 第一層：指令動作日誌符合規格（非只讀 SELECT + UID MOVE 該封）
    moves = sim.uid_commands("move")
    assert len(moves) == 1 and moves[0].args[1:] == (str(INBOX_NEWSLETTER_UID), "Archive")
    assert sim.commands("select")[-1].kwargs.get("readonly") is False

    # 第二層：資料變動合理
    after = sim.snapshot()
    inbox_before = {u for u, _ in before["INBOX"]}
    inbox_after = {u for u, _ in after["INBOX"]}
    assert inbox_before - inbox_after == {INBOX_NEWSLETTER_UID}  # 只少了目標那封
    assert len(after["Archive"]) == 1  # Archive 多一封（新 UID）
    # 其他信箱完全沒動
    for box in ("Sent", "Work/Projects", "台北"):
        assert before[box] == after[box]
    # 使用者自標刪的郵件原封不動仍在 INBOX
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in after["INBOX"]


# ── 雙層驗證：fallback（無 UID MOVE）不誤刪他人 \Deleted ────────────────────

def test_two_layer_fallback_preserves_user_deleted():
    sim = fresh_sim(supports_move=False)
    before_inbox = {u for u, _ in sim.snapshot()["INBOX"]}

    client_on(sim).move(str(INBOX_NEWSLETTER_UID), "Archive", "INBOX")

    # 第一層：fallback 安全序列 copy → store(+\Deleted) → UID EXPUNGE（非整夾 expunge）
    subs = [c.args[0] for c in sim.log if c.name == "uid"]
    assert subs.count("copy") == 1 and subs.count("expunge") == 1
    assert not sim.commands("expunge")  # 沒有走整夾 EXPUNGE
    store = sim.uid_commands("store")[0]
    assert store.args[2] == "+FLAGS" and "Deleted" in store.args[3]

    # 第二層：目標已搬走、使用者自標刪的 106 仍在（未被誤清）
    after_inbox = {u for u, _ in sim.snapshot()["INBOX"]}
    assert INBOX_NEWSLETTER_UID not in after_inbox
    assert INBOX_USER_DELETED_UID in after_inbox
    assert before_inbox - after_inbox == {INBOX_NEWSLETTER_UID}
    assert len(sim.mailboxes["Archive"]) == 1


# ── US1：token 中途過期 → 分類仍全完成（雙層）──────────────────────────────

def test_classify_completes_through_token_expiry(monkeypatch):
    sim = fresh_sim()
    client = connected_client(monkeypatch, sim, token_provider=lambda: "tok")
    sim.arm_expiry(before_op="move", nth=2)  # 第 2 次搬移時 token 過期
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
    assert len([c for c in sim.log if c.name == "authenticate"]) >= 2
    # 第二層：3 封都進 Archive，他人 \Deleted(106) 未波及
    assert len(sim.mailboxes["Archive"]) == 3
    assert (INBOX_USER_DELETED_UID, frozenset({DELETED})) in sim.snapshot()["INBOX"]


# ── US2：效率（用指令日誌抓冗餘）—— 同一流程整夾只讀一次、list 只一次 ──────

def test_classify_reads_each_source_folder_once_via_log():
    sim = fresh_sim()
    client = client_on(sim)
    cache = classifier.ClassifyCache()
    rows = _rows(
        (str(INBOX_NEWSLETTER_UID), "INBOX", "Archive"),
        (str(INBOX_CJK_UID), "INBOX", "Archive"),
    )
    items = classifier.build_report(client, rows, cache=cache)
    classifier.execute(client, items, cache=cache)
    # 指令日誌效率斷言：INBOX 整夾標頭 fetch 只一次（報告讀、執行重用、不二次掃描）
    fetches = [c for c in sim.log if c.name == "uid" and c.args[0] == "fetch"]
    assert len(fetches) == 1
    # 資料夾清單只讀一次（report/new_folders/execute 共用快取）
    assert len(sim.commands("list")) == 1


# ── US1/FR-011：dry-run 不被繞過（即使底層會重連）──────────────────────────

def test_dry_run_moves_nothing_even_with_reconnect(monkeypatch, tmp_path):
    sim = fresh_sim()
    client = connected_client(monkeypatch, sim, token_provider=lambda: "tok")
    before = sim.snapshot()
    p = tmp_path / "w.csv"
    p.write_text(
        f"uid,current_folder,target_folder\n{INBOX_NEWSLETTER_UID},INBOX,Archive\n",
        encoding="utf-8-sig",
    )
    cli.classify(client, str(p), run=False, interactive=False)  # 未確認 → 不搬
    assert sim.snapshot() == before  # 狀態完全不變（dry-run）
    destructive = [c for c in sim.log if c.name == "uid" and c.args[0] in ("move", "copy", "expunge")]
    assert not destructive and not sim.commands("expunge")  # 無任何破壞性動作
