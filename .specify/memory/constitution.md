<!--
Sync Impact Report
- Version change: (uninitialized template) → 1.0.0
- Ratification: initial adoption 2026-06-21 (constitution filled from the de-facto rules in CLAUDE.md §2/§4)
- Principles defined: I. Backend Isolation · II. OAuth-Only Authentication · III. Safe-by-Default Destructive Actions · IV. Secrets Never Leak · V. Test-First Discipline · VI. Crash-Proof & Honest Operation
- Added sections: Locked Technology Stack; Development Workflow & Quality Gates; Governance
- Templates reviewed for alignment:
    ✅ .specify/templates/plan-template.md  (Constitution Check gate compatible)
    ✅ .specify/templates/spec-template.md  (no mandatory-section conflicts)
    ✅ .specify/templates/tasks-template.md (test-first task ordering compatible)
    ✅ CLAUDE.md §2/§4 (source of these principles; now mirrored here)
- Deferred TODOs: none
-->

# MailKeeper Constitution

MailKeeper is a Python CLI that signs in to Outlook.com over IMAP (OAuth2 / XOAUTH2),
lists inbox headers, and organizes mail by user-defined rules. This constitution states
the non-negotiable rules that govern its design and development. It is the source of truth;
CLAUDE.md §2 mirrors it for day-to-day operation.

## Core Principles

### I. Backend Isolation (NON-NEGOTIABLE)

`imaplib` (and any provider-specific protocol detail) MUST appear ONLY in
`src/mailkeeper/imap_client.py`. Upper layers (`cli.py`, `organizer.py`) depend SOLELY on
the `MailBackend` protocol and the `MailHeader` domain dataclass. Adding a provider MUST be
done by writing a new class that implements `MailBackend` — never by editing `organizer.py`
or `cli.py` to special-case a provider. Only domain types cross the seam; raw protocol
responses never do. **Rationale:** the replaceability of the mail backend is the central
design bet; the protocol is the invariant.

### II. OAuth-Only Authentication

Outlook.com Basic Auth is dead. Authentication MUST be OAuth2 / XOAUTH2 via MSAL. Password
login MUST NOT be reintroduced. OAuth scopes MUST stay minimal (`IMAP.AccessAsUser.All`).
**Rationale:** Basic Auth is disabled upstream and is a security regression.

### III. Safe-by-Default Destructive Actions

Anything that moves or deletes mail MUST default to `dry_run=True`; the user opts in to real
changes explicitly. Destructive operations MUST be idempotent where feasible (e.g. move via
`UID MOVE` with a copy+delete+expunge fallback). **Rationale:** an organizer that silently
mutates a mailbox on first run is unacceptable.

### IV. Secrets Never Leak

`token_cache.bin` holds live credentials. It MUST be gitignored, never logged, never echoed
into output, and never committed. User-specific configuration (`config.json`, holding
`client_id`/`email`) MUST be gitignored. The access token MUST NEVER be written into
`config.json` or surfaced by error messages. **Rationale:** credential leakage is the highest-
impact failure for a mail tool.

### V. Test-First Discipline (NON-NEGOTIABLE)

Every behavioral change follows Red → Green → Refactor: write a failing test first, watch it
fail for the right reason, then write the minimal code to pass. Tests MUST run fully offline
by injecting a `FakeBackend` implementing `MailBackend` — no network. No production behavior
ships without a test that first failed. **Rationale:** tests written after the fact pass
immediately and prove nothing.

### VI. Crash-Proof & Honest Operation

For any anticipated failure, the tool MUST NOT crash or hang: user-facing output MUST be
encoding-safe on any host/locale (degrade with a visible placeholder, never raise); header
decoding MUST be best-effort and never raise; network/login waits MUST be time-bounded; and a
CLI error boundary MUST turn anticipated failures into a concise message plus a non-zero exit
(no raw traceback, no secret). Every release MUST bump the version (in `pyproject.toml` AND
`src/mailkeeper/__init__.__version__`) and record a `CHANGELOG.md` entry dated with the REAL
delivery date — never a fabricated one. **Rationale:** worldwide mail guarantees diverse
encodings and flaky networks; honesty in the changelog preserves trust.

## Locked Technology Stack

The stack is fixed: Python ≥ 3.10 · stdlib `imaplib` + `email` · MSAL (OAuth2 / XOAUTH2) ·
`charset-normalizer` (header charset detection) · `setuptools` + `build` (src layout) ·
`pytest` · `mypy`. Identifiers, types, commits, and instruction files are in English;
human-facing comments/output may be in Traditional Chinese. Every module uses
`from __future__ import annotations` and stays `mypy`-clean. Introducing any new runtime
dependency is a stack change and REQUIRES an amendment to this constitution (see Governance).

## Development Workflow & Quality Gates

Every feature or fix flows through **SDD → TDD → SR**, one feature per branch (`NNN-short-name`).

- **SDD (Spec Kit):** `/speckit.specify` (WHAT/WHY) → `/speckit.clarify` (**gate:** resolve
  ambiguity) → `/speckit.plan` (HOW, honoring this constitution and the locked stack) →
  `/speckit.tasks` (ordered, test-first) → `/speckit.analyze` (**gate:** read-only
  consistency/coverage; fix any constitutional violation before implementing).
- **TDD:** Red → Green → Refactor, offline (Principle V).
- **SR (Senior Review):** a dedicated review pass over the actual diff, run as an independent
  reviewer with a context separate from the author. SR assesses contract integrity,
  architecture, security, failure modes, test adequacy, and compatibility, and returns a
  verdict of APPROVE / APPROVE WITH CONDITIONS / REJECT.

**Definition of Done:** spec + plan + tasks committed · `analyze` clean · all tests green
(offline) · `mypy` clean · SR = APPROVE · CHANGELOG updated and version bumped.

## Governance

This constitution supersedes other practices; where CLAUDE.md and this document conflict on a
principle, this document governs (user instructions in CLAUDE.md may still tighten, not
loosen, a rule). Amendments MUST be documented in this file, carry a version bump, and
propagate to dependent artifacts (CLAUDE.md, the `.specify` templates) in the same change.

Versioning of this constitution follows semantic versioning: **MAJOR** for backward-
incompatible governance/principle removals or redefinitions; **MINOR** for a new principle or
materially expanded guidance; **PATCH** for clarifications and wording. Compliance is verified
at the SR gate of every feature; an unjustified violation is a blocking (REJECT-level) finding.

**Version**: 1.0.0 | **Ratified**: 2026-06-21 | **Last Amended**: 2026-06-21
