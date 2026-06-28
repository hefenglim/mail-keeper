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
- **Test adequacy** — behaviors and edge cases covered; the suite runs offline. For seam-crossing code: tests assert the *request we send* (not just reply parsing), use the faithful `FakeIMAPConn` simulator, and check output invariants (see §7). A fixture that fabricates a server reply the code never requested is a red flag, not coverage.
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

**MANDATE — any code interfacing with the `imaplib` layer MUST be tested through the IMAP Simulator Engine,
simulating BOTH normal and abnormal conditions (non-negotiable).** The engine (`tests/imap_server.py::ImapServer`
+ `tests/imap_transport.py::SimIMAP4_SSL`) is the sole sanctioned harness for seam-crossing code: never hand-craft
imaplib replies, never resurrect FakeIMAPConn. Cover the happy path AND fault paths (`arm_expiry(...)`:
eof/oserror/sslerror/bye/authfail). If a needed scenario falls in a `規劃中` gap, **extend the engine first
(with a fidelity case), then write the product test** — never bypass the engine, never fabricate. The engine's
full goals, capability surface, conformance status, and roadmap are the **spec: `doc/imap-simulator-engine-spec.md`**.
Note: engine wire transcripts / `dump()` capture the base64 SASL bearer line — keep it test-only, never feed a real
account or paste real-token transcripts (spec §5.8).

**Backend-seam discipline (non-negotiable — see `doc/lessons-learned.md`).** `imap_client.py` is the only code
touching the real IMAP protocol and is where the highest-risk bugs hide. For ANY code crossing the seam:
- **Test the request, not just the response.** Assert what IMAP command/arguments we send (e.g. FETCH must
  request `UID`), not only how we parse a reply. A parser test fed a fabricated reply proves nothing about the
  contract — that exact gap shipped the 0.5.1 UID-empty bug.
- **Use the wire-level engine, not hand-crafted replies.** Exercise `OutlookIMAPClient` against the **real
  `imaplib.IMAP4_SSL` over the in-memory server engine** — `tests/imap_server.py::ImapServer` (a bytes-in/out
  IMAP server) behind `tests/imap_transport.py::SimIMAP4_SSL`. The product runs **genuine imaplib**; only the
  socket is swapped, so fidelity is automatic and there is no fabrication surface. When you add a new IMAP method,
  extend the engine with the *real* server behavior in lockstep (add a fidelity case first — see below).
- **Assert output invariants.** e.g. every `MailHeader.uid` is non-empty; destructive ops never delete without a
  verified copy. Prefer a loud failure over silent corruption.

**The engine is the bedrock of offline testing — keep it rock-solid (mandatory for new IMAP work). FakeIMAPConn
was retired in P3; `tests/imap_sim.py` now holds only shared wire helpers + the message model:**
1. **Byte-identical fidelity, verified against real imaplib.** The engine's wire output MUST parse correctly
   through the real `imaplib` parser. `tests/test_imap_server.py` section B feeds engine wire through
   `tests/imaplib_probe.py::ScriptedIMAP4` (the real parser) and asserts structure + literal bytes;
   `tests/test_imap_server_behaviors.py` drives raw imaplib over the engine for server-side edge behaviors. When a
   response format is uncertain, **confirm it against the real parser (`imaplib/imaplib.py` v2.60 reference, or a
   real run) — never guess.** Adding a new IMAP response → add a fidelity case first.
2. **Master dataset, copy-per-test.** Start every product-behavior test from
   `tests/imap_dataset.py::fresh_server()` (independent deep copy of a comprehensive master: ASCII/CJK/emoji/
   encoded-word/seen/user-deleted/empty/long subjects, nested + CJK folder names; `bulk_server(n)` for >100-msg
   multi-batch FETCH). Extend the master when a new scenario needs covering. NB: encode a non-ASCII header run as a
   **single** encoded-word — adjacent encoded-words lose interior whitespace via `decode_header`
   (see `imap_sim._encode_header_value`).
3. **Two-layer verification.** After an operation assert BOTH: (1) the command log (`server.log` /
   `server.commands(...)`) — dispatched IMAP commands/args/order are correct and safe; (2) `server.snapshot()`
   before vs after — data mutations are correct (and nothing else changed; e.g. a foreign `\Deleted` message is
   never collaterally expunged).
- **imaplib reference source = `imaplib/imaplib.py` (vendored, v2.60; gitignored).** Whenever you are unsure
  what bytes the simulator must return to the upper layer, **consult that source (or do a real run) — never
  guess.** Caveat: the product actually runs the *stdlib* imaplib (`C:\Python312\Lib\imaplib.py`, 3.12.x); 2.60
  differs only in transport internals (`_readbuf`/`sock.recv` vs `file`), NOT in the parse paths the engine
  targets — and `SimIMAP4_SSL` overrides the transport entirely, so it is version-independent. Fidelity is
  pinned by cross-checking the engine's wire against the **running stdlib** via `tests/imaplib_probe.py`.
- **Loop-regression tests MUST run through the simulator and analyze its log data (non-negotiable).** Any
  bulk-mail / loop behavior (classify/export over many messages, reconnect-mid-loop) is exercised on the engine,
  then asserted via its log analytics: `server.loop_report()` (`redundant_full_folder_reads` must be empty —
  no redundant whole-folder re-fetch; `fetches_per_folder`, `command_counts`, `roundtrips`, `bytes_*` for
  bottleneck analysis), `server.assert_all_fetches_request_uid()` (pins the 0.5.x UID regression class), and the
  before/after `snapshot()`. See `tests/test_imap_loop_regression.py`. The log is also the efficiency oracle —
  it surfaces wasted work (e.g. re-`SELECT`ing an already-selected folder per move) for optimization.
- Keep `imap_client.py` coverage ≥ 88% (CI gate, `.github/workflows/ci.yml`).
- Run `doc/release-smoke.md` (real account) before every release — the only check that hits a real server.

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
- **IMAP Simulator Engine spec (normative — what the engine must be & its conformance status): `doc/imap-simulator-engine-spec.md`**
- IMAP simulator rebuild plan (Option B, P1–P4 history): `doc/imap-simulator-plan.md`
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
at specs/008-bulk-fetch-resilience/plan.md
<!-- SPECKIT END -->
