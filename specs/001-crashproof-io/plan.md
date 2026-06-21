# Implementation Plan: Crash-proof Unicode I/O & Resilient Header Decoding

**Branch**: `001-crashproof-io` | **Date**: 2026-06-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-crashproof-io/spec.md`

## Summary

Harden the presentation, header-decoding, and failure-handling layers so MailKeeper renders correctly and degrades gracefully on any host/locale, decodes worldwide headers best-effort, ends anticipated failures cleanly (no raw traceback), and never hangs (bounded network/login waits). Approach: a small new `console` module that forces UTF-8 output with a non-raising fallback; a hardened `_decode()` using `charset-normalizer` for undeclared/legacy bytes; a CLI top-level error boundary; and a configurable IMAP socket timeout plus a bounded device-flow wait. The `MailBackend` protocol and `MailHeader` type are unchanged.

## Technical Context

**Language/Version**: Python ≥ 3.10
**Primary Dependencies**: `msal` (existing); `charset-normalizer` (new — ratified via the locked-stack amendment in CLAUDE.md, 2026-06-21)
**Storage**: N/A (no change to `token_cache.bin`)
**Testing**: `pytest`, fully offline — a header-sample corpus, a fake non-UTF-8 stdout, and a `FakeBackend`/failing backend
**Target Platform**: Windows non-Unicode consoles, Windows Unicode consoles, redirected/piped output; POSIX hosts
**Project Type**: Single Python package, src layout (CLI tool)
**Performance Goals**: N/A (header-only listing; perf deferred to R7)
**Constraints**: offline-testable; `mypy`-clean; no change to `MailBackend`/`MailHeader`; dry-run/read-only defaults intact; never raise on output/decoding; never hang on anticipated failures

## Constitution Check

*GATE: must pass before design; re-checked after.*

- **Backend isolation** — ✓ `imaplib` stays in `imap_client.py`; the new `console.py` is a presentation helper with no IMAP knowledge; `_decode()` stays in `imap_client.py`. No protocol detail crosses the seam.
- **OAuth only** — ✓ No auth-mechanism change; we only bound the device-flow wait and leave silent refresh untouched.
- **Destructive actions default to dry-run** — ✓ Unchanged; this feature is read-only/presentation.
- **Secrets never leak** — ✓ The error boundary must print messages only, never the token/auth string; tests assert no token material in output.
- **Honest changelog** — ✓ Version bump to 0.2.0 with a real-dated CHANGELOG entry at implementation.
- **Locked stack** — ✓ Amended to include `charset-normalizer` (governance updated in CLAUDE.md). No other new dependency.

**Result**: PASS. No violations; Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/001-crashproof-io/
├── spec.md              # WHAT/WHY (done)
├── plan.md              # This file
├── checklists/
│   └── requirements.md  # spec quality checklist (done)
└── tasks.md             # produced by the tasks step
```

### Source Code (repository root)

```text
src/mailkeeper/
├── console.py        # NEW — UTF-8 output setup + safe_print fallback (presentation helper)
├── imap_client.py    # EDIT — harden _decode(); pass socket timeout to IMAP4_SSL
├── auth.py           # EDIT — bound the device-flow wait; clear timeout messaging
├── cli.py            # EDIT — install console setup; wrap pipeline in the error boundary
├── __main__.py       # EDIT (if needed) — route through the same boundary
├── config.py         # EDIT — add IMAP_TIMEOUT default (consumed here; user-config home arrives in 002)
└── __init__.py       # EDIT — bump __version__

tests/
├── conftest.py       # NEW — FakeBackend, fake non-UTF-8 stdout, header corpus fixtures
├── test_decode.py    # NEW — _decode() corpus + never-raise
├── test_console.py   # NEW — output never raises; degradation policy
└── test_cli_boundary.py  # NEW — anticipated failures → clean message + non-zero exit, no traceback

pyproject.toml        # EDIT — add charset-normalizer dependency; bump version 0.1.0 → 0.2.0
CHANGELOG.md          # EDIT — dated 0.2.0 entry
```

**Structure Decision**: Single-package src layout (existing). The only new module is `console.py`, deliberately isolated so the output policy lives in one testable place and sets up future R6 (logging). Everything else is targeted edits to existing modules.

## Key Technical Decisions (research folded)

1. **Output safety** — On startup (a `console.setup()` called first thing in `cli.main`), reconfigure `sys.stdout`/`sys.stderr` to `encoding="utf-8", errors="backslashreplace"` when the stream exposes `.reconfigure`; otherwise leave the stream and route user output through `console.safe_print`, which catches `UnicodeEncodeError` and re-emits with a backslash-escaped fallback. Rationale: reconfigure is the most bulletproof cross-platform fix; `backslashreplace` preserves information (debuggable) over `replace` (lossy ﹖). The `safe_print` net guarantees FR-001/FR-002 even when reconfigure is unavailable (captured/wrapped streams).

2. **`_decode()` hardening** — (a) Unfold the raw header (collapse folding CRLF + leading whitespace) before `email.header.decode_header` so split multi-segment encoded-words rejoin. (b) For each `(bytes, charset)` chunk: decode with the declared charset; on `LookupError`/`UnicodeDecodeError`, run `charset_normalizer.from_bytes(...).best()` and use its decoding; final fallback decodes with `errors="replace"`. (c) For chunks that arrive already as `str` mojibake (upstream latin-1), re-encode to latin-1 bytes and detect; if detection confidence is low, keep the original string (never worsen). The function always returns `str` and never raises (broad `except` returns best-effort). Satisfies FR-004/005/006.

3. **CLI error boundary** — `cli.main` wraps the pipeline. Catch the anticipated set — MSAL auth errors, `imaplib.IMAP4.error`, `socket.timeout`/`TimeoutError`/`OSError`, and our own config errors — and emit a concise, encoding-safe message to stderr, then `sys.exit(<non-zero>)`. A final catch-all `Exception` prints a short "unexpected error" line (no traceback) and exits non-zero. No exception path prints the token or auth string. Satisfies FR-007/008.

4. **Anti-stuck timeouts** — Pass `timeout=config.IMAP_TIMEOUT` (default 60s) to `imaplib.IMAP4_SSL`; a non-responsive server then raises a timeout that the boundary turns into a clean message. For the device-code wait, MSAL's `acquire_token_by_device_flow` blocks until the code's `expires_in`; we surface a clear up-front message about the bound and, if a tighter ceiling is needed, wrap polling with our own deadline. Silent-refresh path is untouched and not slowed. Satisfies FR-009/010.

## Phased Implementation (TDD — Red → Green → Refactor)

- **Phase 1 — Resilient decoding (US2, P1)**: failing corpus tests for `_decode()` (UTF-8, Big5, GBK/GB2312, ISO-2022-JP, EUC-KR, folded, unknown charset, malformed, undeclared mojibake, empty/None) → implement hardening → refactor.
- **Phase 2 — Crash-proof output (US1, P1)**: failing tests against a simulated non-UTF-8 stdout printing CJK/emoji → implement `console.py` and wire `console.setup()`/`safe_print` into `cli` → refactor.
- **Phase 3 — Graceful failure & timeouts (US3, P2)**: failing tests injecting auth/IMAP/timeout failures via a backend → implement the error boundary and timeouts → refactor.
- **Phase 4 — Packaging/governance**: add `charset-normalizer` to `pyproject.toml`; bump version 0.1.0 → 0.2.0 in `pyproject.toml` and `__init__.py`; dated CHANGELOG entry; `mypy` clean.

## Testing

All offline. Fixtures: a header corpus (bytes + expected/best-effort), a fake stdout whose `.write` raises `UnicodeEncodeError` for out-of-range characters, and a backend that raises selected exceptions. Assertions: decoder never raises and returns expected/best-effort strings; output never raises and degrades per policy; failures produce a clean message + non-zero exit with zero traceback text; IMAP client is constructed with a finite timeout.

## Risks & Mitigations

- **charset-normalizer detection variance** — acceptable; the layered fallback (declared → detected → replace) guarantees a result; tests pin behavior on a fixed corpus.
- **Device-flow hard bound limited by MSAL API** — mitigation: clear messaging plus an optional custom polling deadline; the silent path (the common case) is unaffected.
- **Reconfigure unavailable on wrapped streams** — mitigation: `safe_print` fallback net.

## Notes

- Agent-context `after_plan` hook (`speckit.agent-context.update`) is optional and skipped; the CLAUDE.md SPECKIT marker remains generic.
- Version target for this feature: **0.2.0**.
