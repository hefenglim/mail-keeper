# CLAUDE.md

> Operating guide for **Claude Code** on the MailKeeper project. Read it fully before any task.
> Keep it concise — it loads into context every session. Update it when commands, structure, or rules change.

## 1. What this is
MailKeeper — a Python CLI that logs into Outlook.com over IMAP (OAuth2 / XOAUTH2), lists inbox
subjects, and organizes mail by user-defined rules. Version **0.1.0** · Python **≥ 3.10** · deps: `msal` + `charset-normalizer` + stdlib.

## 2. Prime directives (non-negotiable)
These mirror `.specify/memory/constitution.md`. Never violate them; if a task seems to require it, stop and flag.

- **Backend isolation.** `imaplib` may appear ONLY in `src/mailkeeper/imap_client.py`. Upper layers depend solely on the `MailBackend` protocol and the `MailHeader` dataclass. A new provider = a new class implementing `MailBackend`; never edit `organizer.py` / `cli.py` to special-case a provider.
- **OAuth only.** Outlook.com Basic Auth is dead. Authentication is OAuth2 / XOAUTH2 via MSAL. Never reintroduce password login.
- **Destructive actions default to dry-run.** Anything that moves or deletes mail defaults to `dry_run=True`; the user opts in explicitly.
- **Secrets never leak.** `token_cache.bin` holds live credentials — gitignored, never logged, never echoed into output.
- **Honest changelog.** Every version bump updates `CHANGELOG.md` with the REAL delivery date — never a fabricated one.

## 3. Commands
```bash
pip install -e .             # dev install (editable)
mailkeeper                   # run (after filling client_id/email in ./config.json)
python -m mailkeeper         # equivalent entry point
pytest                       # run tests (place under tests/)
mypy src/mailkeeper          # type check (keep clean)
python -m build              # produce dist/*.whl + *.tar.gz
```

## 4. Development workflow — SDD → TDD → SR
Every feature or fix goes through this pipeline. **One feature per Git branch**, named `NNN-short-name`
(Spec Kit detects the active feature from the branch).

### SDD — Spec Kit (the skeleton)
Drive with `/speckit.*` slash commands; artifacts live under `.specify/`.
1. `/speckit.specify` — define WHAT & WHY only. No implementation detail.
2. `/speckit.clarify` — resolve every ambiguity before planning. **Gate.**
3. `/speckit.plan` — define HOW, honoring this constitution and the locked stack.
4. `/speckit.tasks` — ordered, actionable tasks, written **test-first** (see TDD).
5. `/speckit.checklist` + `/speckit.analyze` — read-only consistency & coverage check. Run **before** implement; fix any constitutional violation it reports. **Gate.**
6. `/speckit.implement` — execute the tasks under TDD discipline.

### TDD — inside `implement`
For each behavioral task, in order:
- **Red** — write a failing test first. Test offline: inject a `FakeBackend` implementing `MailBackend`; no network.
- **Green** — write the minimal code to pass.
- **Refactor** — clean up while tests stay green.

No production behavior ships without a test that first failed for the right reason.

### SR — Senior Review, System-level (final gate before merge)
A separate senior pass over the actual diff — not line-nits, but **system integrity**. Run it as a dedicated
**review subagent** with a reviewer persona and a context independent from the author, to avoid self-justification.

Review dimensions:
- **Contract integrity** — `MailBackend` boundary respected; no `imaplib` leak above the seam; `__init__` public exports intact.
- **Architecture** — layering preserved; no new coupling; swapping the backend is still trivial.
- **Security** — token/credential handling; nothing logged or committed; OAuth scopes stay minimal.
- **Failure modes** — token expiry, disconnects, partial failures; destructive ops are idempotent and dry-run-gated.
- **Test adequacy** — behaviors and edge cases covered; the suite runs offline.
- **Compatibility** — CHANGELOG updated, version bumped, no silent breaking change.

Verdict: **APPROVE** / **APPROVE WITH CONDITIONS** (enumerate them) / **REJECT** (list required changes).
Merge only on APPROVE.

### Definition of Done
spec + plan + tasks committed · `analyze` clean · all tests green (offline) · `mypy` clean · **SR = APPROVE** · CHANGELOG + version bumped.

## 5. Architecture
Four layers, top-down: `cli.py` → `organizer.py` (rule engine) → **`MailBackend`** (protocol — the stable seam) →
backend (`imap_client.py` today; `graph_client.py` later). `auth.py` supplies the OAuth token out of band.
The protocol is the invariant; everything below it is replaceable. Full map: `MailKeeper-Handoff.html`.

## 6. Code conventions
- English for identifiers, types, commits, and instruction files; Traditional Chinese is fine for human-facing comments/output.
- `from __future__ import annotations` in every module; keep mypy-clean.
- Cross the seam only with domain types (`MailHeader`) — never raw IMAP responses.
- Rule predicates are small and pure (`from_contains`, `subject_contains`); compose, don't special-case.

## 7. Testing
`pytest` under `tests/`. Inject a `FakeBackend` (implements `MailBackend`) into `MailOrganizer` to test rule
matching and action sequences with no network. Also cover `_decode()` on MIME encoded-word headers and the
dry-run vs. real-run action sets.

## 8. Repo etiquette
- Branch per feature: `NNN-name`.
- Imperative commit subjects.
- Bump `version` in `pyproject.toml` **and** `src/mailkeeper/__init__.__version__` together with the CHANGELOG entry.
- Never commit `token_cache.bin`, `dist/`, `build/`, or `*.egg-info/`.

## 9. Setup notes
One-time Azure / Microsoft Entra app registration is required: personal Microsoft accounts, allow public client
flows, delegated scope `IMAP.AccessAsUser.All`. Put `client_id` and your mailbox `email` in
`config.json` in the working directory (auto-generated on first run, alongside `token_cache.bin`).
`config.py` now holds only non-secret code defaults (authority/scopes/IMAP host/port/timeout).
Full steps in `MailKeeper-Handoff.html` and `README.md`.

## 10. References
- Constitution (source of truth for §2): `.specify/memory/constitution.md`
- Specs / plans / tasks: `.specify/specs/NNN-*/`
- Handoff & architecture diagram: `MailKeeper-Handoff.html`
- Roadmap: handoff §11 — R1 CLI args → R2 tests → R3 Graph backend → R4 config externalization → …

---
<!-- Spec Kit's /speckit.plan may maintain "Active Technologies" / "Recent Changes" sections below. Leave room for them; avoid hand-editing once Spec Kit manages them. -->
## Active stack
- Python ≥ 3.10 · stdlib `imaplib` + `email` · MSAL (OAuth2 / XOAUTH2) · `charset-normalizer` (header charset detection, added 2026-06-21 for feature 001) · setuptools + build (src layout) · pytest · mypy

<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan
<!-- SPECKIT END -->
