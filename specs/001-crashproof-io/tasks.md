# Tasks: Crash-proof Unicode I/O & Resilient Header Decoding

**Input**: Design documents from `specs/001-crashproof-io/`
**Prerequisites**: [plan.md](./plan.md) (required), [spec.md](./spec.md) (user stories)
**Tests**: REQUIRED — TDD is mandated by CLAUDE.md §4 and spec SC-005 (offline suite). Every behavioral task is written test-first (Red → Green → Refactor).

**Organization**: Tasks grouped by user story (US1/US2/US3) for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no incomplete-task dependency)
- All paths are repository-relative.

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Create `tests/conftest.py` with shared fixtures: a `FakeBackend` implementing `MailBackend`, a `fake_non_utf8_stdout` helper whose `write()` raises `UnicodeEncodeError` for out-of-range characters, and a MIME header-sample corpus (bytes + expected/best-effort text) — in `tests/conftest.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Blocks US2 implementation.**

- [x] T002 Add `charset-normalizer>=3` to `[project].dependencies` in `pyproject.toml`; reinstall editable so decoding can use it — in `pyproject.toml`

**Checkpoint**: dependency available; stories can proceed.

---

## Phase 3: User Story 1 — Crash-proof output (Priority: P1) 🎯 MVP

**Goal**: All user-facing output renders on any host or degrades with a visible placeholder; never raises an encoding error.
**Independent Test**: Print CJK/emoji/Korean to a non-UTF-8 target → every line emitted, no crash, success exit.

### Tests (write first, must FAIL)

- [x] T003 [P] [US1] Failing unit tests: `console.safe_print` of CJK/emoji to `fake_non_utf8_stdout` never raises and emits backslash-escaped placeholders; `console.setup()` reconfigures to UTF-8 when available and is safe when not — in `tests/test_console.py`
- [x] T004 [P] [US1] Failing integration test: running the listing + dry-run pipeline with CJK/emoji headers against `fake_non_utf8_stdout` completes without raising and exits success; **also include a UTF-8 target case asserting correct rendering with no escape/placeholder substitution (FR-003, finding A4)** — in `tests/test_cli_output.py`

### Implementation

- [x] T005 [US1] Implement `console.py`: `setup()` rebinds `sys.stdout`/`sys.stderr` to UTF-8 with `errors="backslashreplace"` (via `.reconfigure` when present, else a `TextIOWrapper` over `.buffer`, else leave intact), and `safe_print()` that catches `UnicodeEncodeError` and re-emits escaped — in `src/mailkeeper/console.py`
- [x] T006 [US1] Call `console.setup()` as the first action in `main()`; route status/error lines through `console.safe_print` — in `src/mailkeeper/cli.py`

**Checkpoint**: output is crash-proof end-to-end (plain `print()` in `organizer.py` is covered by `setup()` rebinding; no organizer change needed).

---

## Phase 4: User Story 2 — Resilient worldwide decoding (Priority: P1)

**Goal**: Decode standard encoded-words (incl. folded/multi-segment) to readable text across world encodings; degrade gracefully for undeclared/malformed; never raise.
**Independent Test**: Feed the header corpus to `_decode()` → never raises, standard cases readable, undecodable cases best-effort/placeholder.

### Tests (write first, must FAIL)

- [x] T007 [P] [US2] Failing corpus tests for `_decode()`: UTF-8, Big5, GBK/GB2312, ISO-2022-JP, EUC-KR, folded multi-segment, unknown charset, malformed bytes, undeclared mojibake, `None`, empty — assert never raises and expected/best-effort output — in `tests/test_decode.py`

### Implementation

- [x] T008 [US2] Harden `_decode()`: unfold folding whitespace before `decode_header`; per-chunk decode with declared charset; on `LookupError`/`UnicodeDecodeError` fall back to `charset_normalizer` best-guess on the raw bytes, then `errors="replace"`; for already-`str` mojibake re-encode latin-1 → detect, keep original if low confidence; always return `str`, never raise — in `src/mailkeeper/imap_client.py` (depends on T002)

**Checkpoint**: US1 + US2 both independently functional.

---

## Phase 5: User Story 3 — Graceful failure & no hang (Priority: P2)

**Goal**: Anticipated failures end with a concise message + non-zero exit (no traceback, no token); network/login waits are bounded.
**Independent Test**: Inject auth/IMAP/timeout failures → clean message + non-zero exit, no traceback; IMAP client built with a finite timeout.

### Tests (write first, must FAIL)

- [x] T009 [P] [US3] Failing tests: a backend raising MSAL auth error / `imaplib.IMAP4.error` / `socket.timeout` makes `main()` print a concise stderr message + non-zero exit, with no traceback text and no token/auth string in output — in `tests/test_cli_boundary.py`

### Implementation

- [x] T010 [US3] Add `IMAP_TIMEOUT = 60` default — in `src/mailkeeper/config.py` (code default here; full user override via `config.json` arrives in feature 002 — FR-009, finding A2)
- [x] T011 [US3] Pass `timeout=config.IMAP_TIMEOUT` to `imaplib.IMAP4_SSL(...)` — in `src/mailkeeper/imap_client.py`
- [x] T012 [US3] Bound the device-flow wait and surface a clear timeout message; leave the silent-refresh path untouched — in `src/mailkeeper/auth.py` (verify the bound via an injected polling deadline offline, or mark manual-verify if MSAL blocking cannot be unit-tested — finding A1)
- [x] T013 [US3] Implement the error boundary in `main()`: catch the anticipated set (auth, `IMAP4.error`, `socket.timeout`/`TimeoutError`/`OSError`, config) → concise `safe_print` to stderr + `sys.exit(non-zero)`; final catch-all → short message, no traceback; never echo the token — in `src/mailkeeper/cli.py`

**Checkpoint**: all three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting

- [x] T014 [P] Bump version `0.1.0` → `0.2.0` in `pyproject.toml` and `src/mailkeeper/__init__.py`
- [x] T015 [P] Add a real-dated `0.2.0` entry to `CHANGELOG.md` describing the I/O/decoding/timeout hardening
- [x] T016 Run `mypy src/mailkeeper` and `pytest` (offline); fix typing/failures until both are clean

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002)** → user stories.
- **US1 (T003–T006)** and **US2 (T007–T008)** are independent (different files: `console.py`/`cli.py` vs `imap_client._decode`); either order, both P1. US2 impl depends on T002.
- **US3 (T009–T013)** independent; `cli.py` boundary (T013) should land after US1's `console.setup` (T005/T006) so messages are encoding-safe.
- **Polish (T014–T016)** last.

## Parallel Opportunities

- T003 and T004 [P]; T007 [P]; T009 [P]; T014 and T015 [P].
- Within a story, write the failing test(s) first, then implement.

## Implementation Strategy

MVP = US1 (crash-proof output) — the most severe defect. Then US2 (readable global mail), then US3 (clean failure/no hang), then polish (version/CHANGELOG/mypy). Commit after each task or logical group; verify each test fails for the right reason before implementing.
