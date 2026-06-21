# Tasks: Externalized config.json with Onboarding & Account-Mismatch Verification

**Input**: Design documents from `specs/002-config-onboarding/`
**Prerequisites**: [plan.md](./plan.md) (required), [spec.md](./spec.md) (user stories). **Depends on feature 001** (error boundary, console output, `IMAP_TIMEOUT` default) — implement 001 first.
**Tests**: REQUIRED — TDD per CLAUDE.md §4 and spec SC-006 (offline suite). Test-first throughout.

**Organization**: Tasks grouped by user story (US1/US2/US3/US4).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no incomplete-task dependency)
- All paths are repository-relative.

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Extend `tests/conftest.py` with: a `tmp_cwd` fixture (temp working directory), a `fake_identity` fixture returning a chosen authenticated email without MSAL/network, and `isatty`/`input` monkeypatch helpers for interactive vs non-interactive — in `tests/conftest.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Blocks all stories.**

- [x] T002 Define program defaults and placeholder sentinels in `config.py`: keep `AUTHORITY`, `SCOPES`, default `IMAP_HOST`/`IMAP_PORT`, `IMAP_TIMEOUT`, `TOKEN_CACHE_PATH`; standardize the mailbox field as `email` (the config.json key, mapped to the effective config) superseding the legacy `EMAIL_ACCOUNT` constant; add placeholder sentinels `CLIENT_ID="YOUR-AZURE-APP-CLIENT-ID"` and `email="your-name@outlook.com"` — in `src/mailkeeper/config.py` (X2 naming standardization)

**Checkpoint**: defaults/sentinels available to the loader.

---

## Phase 3: User Story 1 — First-run guided setup (Priority: P1) 🎯 MVP

**Goal**: Empty working dir → generate a guided `config.json`, print what to fill, exit non-zero without login.
**Independent Test**: Run in an empty `tmp_cwd` → `config.json` created with required fields + guidance, console explains setup, non-success exit, no login attempt.

### Tests (write first, must FAIL)

- [x] T003 [P] [US1] Failing tests: in empty `tmp_cwd`, bootstrap writes strict-JSON `config.json` containing `client_id`/`email` placeholders plus `_README`/`_help_url`, prints guidance, and signals non-zero without attempting login — in `tests/test_config_store.py`

### Implementation

- [x] T004 [US1] Implement `config_store.bootstrap()`: write the strict-JSON template (placeholders + guidance keys) atomically and return/raise a "not configured" signal with console guidance — in `src/mailkeeper/config_store.py`
- [x] T005 [US1] In `main()`, attempt config load; when `config.json` is absent, run bootstrap and exit non-zero before any auth — in `src/mailkeeper/cli.py`

**Checkpoint**: first-run onboarding works.

---

## Phase 4: User Story 2 — Configuration from the working directory (Priority: P1)

**Goal**: Read `CLIENT_ID`/email (and optional IMAP host/port/timeout overrides) from `config.json` in the cwd; no source edits.
**Independent Test**: Valid `config.json` → tool uses it; overrides applied; omitted optionals → defaults.

### Tests (write first, must FAIL)

- [x] T006 [P] [US2] Failing tests: valid `config.json` yields effective `client_id`/`email`; optional `imap_host`/`imap_port`/`timeout` overrides applied; omitted → defaults; `_`-prefixed keys ignored; `AUTHORITY`/`SCOPES` not sourced from JSON — in `tests/test_config_store.py`

### Implementation

- [x] T007 [US2] Implement `config_store.load()`: read `config.json` from `Path.cwd()`, merge over `config.py` defaults, return an effective `Configuration` — in `src/mailkeeper/config_store.py`
- [x] T008 [US2] Wire `cli.py` (and through it `auth`/`imap_client`) to consume the effective configuration (`client_id`, `email`, IMAP host/port, timeout) instead of module constants — in `src/mailkeeper/cli.py`

**Checkpoint**: US1 + US2 functional; configuration fully externalized.

---

## Phase 5: User Story 3 — Unfilled/placeholder config caught clearly (Priority: P2)

**Goal**: Stop before login when a required field is empty/placeholder or the file is unparseable; name the field/file.
**Independent Test**: Empty/placeholder required field or bad JSON → specific message + non-success exit, no login, no traceback.

### Tests (write first, must FAIL)

- [x] T009 [P] [US3] Failing tests: empty/whitespace/sentinel `client_id` or `email` → message naming the field and file path, non-zero, no login; malformed JSON → clean error (via 001 boundary) with no traceback — in `tests/test_config_store.py`

### Implementation

- [x] T010 [US3] Add validation to `config_store.load()`: detect missing/placeholder required fields and parse errors; raise typed, message-bearing errors consumed by the 001 error boundary — in `src/mailkeeper/config_store.py`

**Checkpoint**: misconfiguration fails fast and legibly.

---

## Phase 6: User Story 4 — Account-mismatch verification (Priority: P2)

**Goal**: After auth, if authenticated email ≠ configured email, prompt (use logged-in [+write-back] / keep configured / abort) interactively, or safely abort when non-interactive.
**Independent Test**: Mismatch + interactive → each choice resolves correctly (incl. write-back); mismatch + non-interactive → safe abort; match → no prompt.

### Tests (write first, must FAIL)

- [x] T011 [P] [US4] Failing tests using `fake_identity` + monkeypatched `isatty`/`input`: match → no prompt; interactive choices (a) use-Y with write-back updates only the email and keeps valid JSON, (b) keep-X continues with X, (c) abort exits non-zero; non-interactive mismatch → safe abort with guidance; **and assert the post-write-back `config.json` contains no token/credential field (FR-017, finding A3)** — in `tests/test_mismatch.py`

### Implementation

- [x] T012 [US4] Extend `auth` to expose the authenticated account email (from the MSAL cached account `username`) alongside the token — in `src/mailkeeper/auth.py`
- [x] T013 [US4] Implement mismatch verification: compare (case-insensitive); interactive prompt (gated on `stdin`/`stdout` `isatty`) with the three choices; non-interactive → safe abort; atomic write-back (temp + `os.replace`) updating only `email` — in `src/mailkeeper/config_store.py`
- [x] T014 [US4] In `cli.py`, run verification after auth and select the effective `user=` (configured email by default; authenticated email when the user chooses it) before building the IMAP client — in `src/mailkeeper/cli.py`

**Checkpoint**: all four stories independently functional.

---

## Phase 7: Polish & Cross-Cutting

- [x] T015 [P] Add `config.json` to `.gitignore`
- [x] T016 [P] Update docs to make `config.json` the edit point: `README.md`, `MailKeeper-Handoff.html` setup section, and `CLAUDE.md` §9 — (also note `.specify/memory/constitution.md` is an unfilled stub)
- [x] T017 [P] Bump version `0.2.0` → `0.3.0` in `pyproject.toml` and `src/mailkeeper/__init__.py`
- [x] T018 [P] Add a real-dated `0.3.0` entry to `CHANGELOG.md`
- [x] T019 Run `mypy src/mailkeeper` and `pytest` (offline) until clean

---

## Dependencies & Execution Order

- **Feature 001 complete** → **Setup (T001)** → **Foundational (T002)** → stories in order.
- **US1 (T003–T005)** → **US2 (T006–T008)**: US2 builds on the loader introduced in US1's `config_store`.
- **US3 (T009–T010)** extends the loader from US2.
- **US4 (T011–T014)** depends on T012 (auth identity) and the loader; integrates in `cli` last.
- **Polish (T015–T019)** last.

## Parallel Opportunities

- T003/T006/T009/T011 [P] (test files); T015–T018 [P] (independent files). Implementation tasks touching `config_store.py`/`cli.py` are sequential (same files).

## Implementation Strategy

MVP = US1 + US2 (externalized config with onboarding) — removes the "edit/rebuild source" workflow and the placeholder-in-wheel bug class. Then US3 (fail-fast on bad config), then US4 (mismatch verification — the guard against the original confusing failure). Test-first each story; commit per task/group.
