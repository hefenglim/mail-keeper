# Implementation Plan: Externalized config.json with Onboarding & Account-Mismatch Verification

**Branch**: `002-config-onboarding` | **Date**: 2026-06-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-config-onboarding/spec.md`

## Summary

Move user-specific settings out of source into a working-directory `config.json` (co-located with `token_cache.bin`): required `CLIENT_ID` and mailbox email, plus optional overrides for IMAP host/port and the network timeout. Add a first-run bootstrap that generates a guided template and stops; detect unfilled/placeholder fields before any login; and, after authentication, verify the configured email against the OAuth-authenticated identity — prompting interactively (use logged-in / keep configured / abort, with optional write-back) or aborting safely when non-interactive. `config.py` becomes program defaults plus a loader; `auth` is extended to expose the authenticated account email. This feature builds on feature 001's error boundary and console output, and gives 001's configurable timeout its user-facing home.

## Technical Context

**Language/Version**: Python ≥ 3.10
**Primary Dependencies**: `msal` (existing); `charset-normalizer` (from 001); configuration via stdlib `json` — no new dependency
**Storage**: `config.json` in the current working directory (plain JSON); `token_cache.bin` unchanged
**Testing**: `pytest`, fully offline — temporary working dirs, a fake authenticated-identity provider, and simulated interactive/non-interactive sessions
**Target Platform**: same as 001 (Windows + POSIX, interactive and piped)
**Project Type**: Single Python package, src layout (CLI tool)
**Performance Goals**: N/A
**Constraints**: depends on feature 001 (error boundary, console output, timeout default); CWD-relative discovery; `mypy`-clean; no change to `MailBackend`/`MailHeader`; never crash/hang (inherits 001's guarantees)

## Constitution Check

*GATE: must pass before design; re-checked after.*

- **Backend isolation** — ✓ Configuration/identity logic lives in `config`/`cli`/`auth`; no IMAP/protocol detail crosses the seam; `MailBackend`/`MailHeader` unchanged.
- **OAuth only** — ✓ Identity verification reads the MSAL-authenticated account; no password path introduced.
- **Destructive actions default to dry-run** — ✓ Unchanged. Config write-back edits only `config.json` (not mail) and is gated behind an explicit user choice.
- **Secrets never leak** — ✓ `config.json` holds non-secret `CLIENT_ID`/email but is git-ignored as user-specific; `token_cache.bin` rules unchanged; the token is never written to `config.json` or logged.
- **Honest changelog** — ✓ Version bump to 0.3.0 with a real-dated CHANGELOG entry at implementation.
- **Locked stack** — ✓ Uses stdlib `json` only; no new dependency.

**Result**: PASS. No violations; Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/002-config-onboarding/
├── spec.md
├── plan.md              # This file
├── checklists/
│   └── requirements.md
└── tasks.md             # produced by the tasks step
```

### Source Code (repository root)

```text
src/mailkeeper/
├── config_store.py   # NEW — load/merge/validate config.json; bootstrap template; placeholder detection; atomic write-back
├── config.py         # EDIT — keep code defaults (AUTHORITY, SCOPES, IMAP host/port default, IMAP_TIMEOUT default, token cache path)
├── auth.py           # EDIT — expose the authenticated account email (e.g. return identity alongside token)
├── cli.py            # EDIT — orchestrate: load/bootstrap config → auth → mismatch verification → build client with effective email + timeout
└── __init__.py       # EDIT — bump __version__

tests/
├── test_config_store.py   # NEW — no file→bootstrap; missing/placeholder; valid; optional overrides; bad JSON
└── test_mismatch.py       # NEW — match→no prompt; interactive 3 choices (incl. write-back); non-interactive→safe abort

.gitignore            # EDIT — add config.json
pyproject.toml        # EDIT — bump version 0.2.0 → 0.3.0
CHANGELOG.md          # EDIT — dated 0.3.0 entry
README.md / MailKeeper-Handoff.html / CLAUDE.md §9  # EDIT — config.json is the new edit point
```

**Structure Decision**: A new `config_store.py` owns all config.json I/O and validation, keeping `config.py` as pure code defaults and keeping `cli.py` orchestration thin. This isolates the file format and bootstrap behavior behind a small, testable surface.

## Data Model

- **Configuration** (effective settings used at runtime):
  - *Required (from config.json)*: `client_id`, `email`.
  - *Optional overrides (config.json, else default)*: `imap_host`, `imap_port`, `timeout`.
  - *Fixed (code defaults; NOT user-editable)*: `authority`, `scopes`, `token_cache_path`.
  - Built by merging `config.json` over code defaults. Keys beginning with `_` (e.g. `_README`, `_help_url`) are guidance-only and ignored by the loader.
  - *Placeholder sentinels* (treated as "unset"): `client_id == "YOUR-AZURE-APP-CLIENT-ID"`, `email == "your-name@outlook.com"`, plus empty/whitespace.
- **Authenticated Identity**: the email the OAuth token represents, read from the MSAL cached account (`account["username"]`); used only to compare against the configured email.

## Key Technical Decisions

1. **Discovery** — `config.json` at `Path.cwd() / "config.json"`, co-located with the token cache (same relative-to-CWD behavior). No upward search.
2. **Bootstrap** — When absent: write a strict-JSON template (placeholder values + `_README` + `_help_url`), print full console guidance (required fields, how to register an Azure app for `CLIENT_ID`, file location), and exit non-zero (e.g. code 2) without attempting login (FR-005/006).
3. **Validation** — Required field empty/whitespace/equal-to-sentinel → message naming the field and the file path, exit non-zero, no login (FR-007). Unparseable JSON → clear message via 001's error boundary, no traceback (FR-008). Invalid optional values (non-numeric port/timeout) → clear error.
4. **Mismatch verification** — After token acquisition, obtain the authenticated email; compare case-insensitively to the configured email. Equal → proceed silently (FR-010). Differ + interactive (`sys.stdin.isatty() and sys.stdout.isatty()`) → prompt: (a) use logged-in account [+ optional write-back], (b) keep configured, (c) abort (FR-011). Differ + non-interactive → safe abort with guidance (FR-012). Never silently pick (the never-stuck/never-crash spirit).
5. **Effective `user=`** — Defaults to the configured email; switches to the authenticated email only for the run where the user picks option (a); write-back persists it (FR-013).
6. **Write-back** — Update only the `email` field; serialize and replace via a temp file + `os.replace` (atomic; never corrupt on failure) (FR-014).
7. **Ignore rules** — Add `config.json` to `.gitignore` (FR-015).

## Phased Implementation (TDD — Red → Green → Refactor)

- **Phase 1 — Loader + bootstrap (US1/US2, P1)**: failing tests (empty dir → template created + non-zero exit; valid file loads; optional overrides applied; defaults when omitted) → implement `config_store` load/merge/bootstrap → refactor.
- **Phase 2 — Placeholder/parse detection (US3, P2)**: failing tests (empty/placeholder field; bad JSON) → implement validation → refactor.
- **Phase 3 — Mismatch verification (US4, P2)**: failing tests (match→no prompt; each interactive choice incl. write-back; non-interactive→abort) using a fake identity provider → implement detection + prompt + write-back → refactor.
- **Phase 4 — Wiring/governance**: extend `auth` to expose identity; wire `cli` orchestration; `.gitignore`; update docs (README/handoff/CLAUDE.md §9); bump version 0.2.0 → 0.3.0; dated CHANGELOG; `mypy` clean.

## Testing

All offline. Use `tmp_path` working directories; a fake identity provider returning a chosen authenticated email (no MSAL/network); and simulated TTY/non-TTY via monkeypatched `isatty`/input. Assert: bootstrap creates a loadable template and exits non-zero; missing/placeholder/bad-JSON stop before login and name the field/file; match yields no prompt; each mismatch choice yields the right effective email and (for write-back) a valid updated file; non-interactive mismatch aborts safely.

## Risks & Mitigations

- **Relative-CWD config footgun** (running elsewhere → different config/cache) — documented; the mismatch verification specifically catches the "wrong account" symptom this can cause.
- **`isatty` unreliable in some shells/CI** — safe default is to treat as non-interactive and abort with guidance, never to guess.
- **Ordering dependency on 001** — implement 001 first; 002 relies on its error boundary/console and timeout default.

## Notes

- Agent-context `after_plan` hook is optional and skipped.
- Version target for this feature: **0.3.0** (sequenced after 001's 0.2.0).
- `.specify/memory/constitution.md` is currently an unfilled template; the operative governance for §2 / locked stack lives in CLAUDE.md. Filling the constitution properly is out of scope for this feature (flagged for the user).
