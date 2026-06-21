# Feature Specification: Externalized config.json with Onboarding & Account-Mismatch Verification

**Feature Branch**: `002-config-onboarding`

**Created**: 2026-06-21

**Status**: Draft

**Input**: User description: "R4 如果 token 確認正確，但是 token 取得的 Email 與 config email 設定不同，進行提問，向用戶詢問是否填錯？或是 config email 是否需要修正的疑問？告知 MailKeeper 發想一個錯誤，需要用戶協助確認。然後 client-id 與 email 設定是否可以在 mailkeeper 的執行工作目錄下獨立 config.json 進行設定與讀取？與 token_cache.bin 同一個目錄，首次執行沒有發現 config.json 則告知用戶需要做什麼資訊的設定，並且自動產生 config.json 讓用戶自行填入相關資訊。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - First-run guided setup (Priority: P1)

A new user installs MailKeeper and runs it for the first time in a working directory that has no configuration yet. Instead of failing cryptically, the tool tells the user exactly what information is required and how to obtain it, creates a ready-to-edit `config.json` in that directory, and stops so the user can fill it in.

**Why this priority**: Without configuration the tool cannot do anything, and today the required values are buried in source code. A guided first run is the entry point for every new user and the foundation the other stories build on.

**Independent Test**: Run the tool in an empty working directory; confirm a `config.json` is created there with the required fields and embedded guidance, that the console prints what to fill in and where to get it, and that the tool exits without attempting to log in.

**Acceptance Scenarios**:

1. **Given** a working directory with no `config.json`, **When** the tool is run, **Then** a `config.json` template is created in that directory, the console explains the required fields and how to obtain them, and the tool exits with a non-success status without attempting login.
2. **Given** the freshly generated `config.json`, **When** the user opens it, **Then** it contains the required fields plus embedded guidance (a readme note and a help link) so the user knows what to enter.
3. **Given** the user fills in valid values and re-runs, **When** the tool starts, **Then** it loads the configuration and proceeds normally.

---

### User Story 2 - Configuration is read from the working directory (Priority: P1)

A user keeps their `CLIENT_ID` and mailbox address in a `config.json` alongside `token_cache.bin` in their working directory, rather than editing program source. They can also optionally override advanced settings (IMAP host/port and the network timeout). They never have to modify installed code to configure or reconfigure the tool.

**Why this priority**: Externalizing configuration is the core of this feature; it removes the need to edit (and rebuild) source, prevents the placeholder-shipped-in-a-wheel class of bug, and gives the configurable timeout from the companion I/O feature a home.

**Independent Test**: Place a valid `config.json` in the working directory and confirm the tool uses those values; place one that overrides an optional advanced setting and confirm the override takes effect; omit optional settings and confirm sensible defaults apply.

**Acceptance Scenarios**:

1. **Given** a valid `config.json` with `CLIENT_ID` and email, **When** the tool runs, **Then** it authenticates using those values without any source edits.
2. **Given** a `config.json` that overrides an optional advanced setting (e.g., timeout or IMAP host/port), **When** the tool runs, **Then** the overridden value is used.
3. **Given** a `config.json` that omits the optional advanced settings, **When** the tool runs, **Then** documented defaults are applied.
4. **Given** authentication-critical settings (authority, scopes), **When** configuring, **Then** they are NOT user-editable via `config.json` (they remain fixed defaults).

---

### User Story 3 - Unfilled or placeholder configuration is caught clearly (Priority: P2)

A user creates or copies a `config.json` but leaves a required field blank or with its placeholder value. The tool detects this before trying to log in and tells the user precisely which field is unset and where the file lives, rather than failing later with a confusing authentication or connection error.

**Why this priority**: This converts a whole class of late, confusing failures (like the earlier "authenticated but not connected") into an immediate, actionable message at the configuration boundary.

**Independent Test**: Provide a `config.json` whose required field is empty or still equals the template placeholder; confirm the tool reports the specific field and file path and exits without attempting login.

**Acceptance Scenarios**:

1. **Given** a `config.json` whose `CLIENT_ID` or email is empty or still the template placeholder, **When** the tool runs, **Then** it reports which field is unset and the file path, and exits non-success without attempting login.
2. **Given** a malformed (unparseable) `config.json`, **When** the tool runs, **Then** it reports the problem clearly and exits non-success, without a raw stack trace.

---

### User Story 4 - Account-mismatch verification (Priority: P2)

A user has authenticated successfully, but the account they actually logged in as differs from the email configured in `config.json`. The tool surfaces this discrepancy and asks the user to help resolve it — did they mistype the configured email, do they want it corrected, or is the difference intentional? — instead of silently proceeding (which previously produced a confusing failure).

**Why this priority**: A valid token for the "wrong" mailbox is exactly what caused the original confusing failure. Catching the divergence and asking the user prevents silently operating on an unintended account and makes the configured email a meaningful guard.

**Independent Test**: With a valid token whose authenticated email differs from the configured email, confirm that in an interactive session the tool presents the discrepancy with clear choices, and that in a non-interactive session it aborts safely with guidance; with matching emails confirm no prompt appears.

**Acceptance Scenarios**:

1. **Given** a valid token whose authenticated email differs from the configured email, **When** the tool runs interactively, **Then** it states both addresses and offers to (a) use the logged-in account — optionally writing it back to `config.json`, (b) keep the configured email and continue, or (c) abort to fix it.
2. **Given** the same mismatch but a non-interactive session (no usable prompt), **When** the tool runs, **Then** it aborts safely with a message explaining the discrepancy and how to fix it — it does not silently guess.
3. **Given** the configured and authenticated emails match, **When** the tool runs, **Then** no mismatch prompt appears and the tool proceeds normally.
4. **Given** the user chooses to write back the logged-in account, **When** that choice is made, **Then** only the email field in `config.json` is updated and the file remains valid.

---

### Edge Cases

- Running from a different working directory than where `config.json`/`token_cache.bin` live → configuration (and cache) are resolved relative to the current working directory; a missing file triggers first-run setup in that directory.
- `config.json` present but not readable/parseable (corrupt JSON, wrong types).
- Required field present but empty, whitespace-only, or still equal to the generated placeholder.
- Optional advanced settings present with invalid values (e.g., non-numeric port/timeout).
- Mismatch prompt encountered when input is redirected/piped or no terminal is attached.
- Write-back requested but the file or directory is read-only.
- The authenticated identity cannot be determined from the token provider (treat as: cannot verify → proceed with configured email, or surface clearly).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST read configuration from a `config.json` located in the current working directory, the same directory used for the token cache.
- **FR-002**: `config.json` MUST provide the required fields `CLIENT_ID` and the mailbox email address, and MAY provide optional overrides for IMAP host, IMAP port, and the network timeout.
- **FR-003**: Authentication-critical settings (authority and scopes) MUST NOT be configurable via `config.json`; they remain fixed program defaults.
- **FR-004**: When optional settings are absent, the system MUST apply documented defaults; when present, the system MUST use the provided values.
- **FR-005**: When no `config.json` exists in the working directory, the system MUST generate a template `config.json` there, MUST print clear console guidance describing the required fields and how to obtain them, MUST NOT attempt login, and MUST exit with a non-success status.
- **FR-006**: The generated `config.json` MUST be valid, strict JSON and MUST include embedded guidance fields (a human-readable note and a help link) in addition to the placeholder values.
- **FR-007**: When a required field is empty, whitespace-only, or still equal to the generated placeholder value, the system MUST report which field is unset and the configuration file path, MUST NOT attempt login, and MUST exit with a non-success status.
- **FR-008**: When `config.json` is present but cannot be parsed, the system MUST report the problem clearly and exit non-success without a raw stack trace.
- **FR-009**: After successful authentication, the system MUST determine the authenticated account's email from the identity provider and compare it to the configured email.
- **FR-010**: When the authenticated email and configured email match, the system MUST proceed without any prompt.
- **FR-011**: When they differ and an interactive session is available, the system MUST present both addresses and offer to: (a) use the authenticated account, with an option to write it back to `config.json`; (b) keep the configured email and continue; or (c) abort.
- **FR-012**: When they differ and no interactive prompt is available, the system MUST abort safely with an explanatory message and guidance, and MUST NOT silently choose an account.
- **FR-013**: By default the mailbox identity used for the connection MUST be the configured email; it MUST switch to the authenticated email only when the user explicitly chooses that in the mismatch prompt.
- **FR-014**: Writing back the authenticated email MUST update only the email field, MUST keep `config.json` valid, and MUST be performed safely (without corrupting the file on failure).
- **FR-015**: `config.json` MUST be excluded from version control (added to ignore rules), consistent with treating it as user-specific local configuration.
- **FR-016**: These changes MUST preserve the backend isolation contract — no change to the `MailBackend` interface or `MailHeader`, and no IMAP/protocol detail leaking above the seam.
- **FR-017**: The token cache file (live credentials) MUST continue to be treated as a secret: never written into `config.json`, never logged, never committed.

### Key Entities *(include if feature involves data)*

- **Configuration**: The effective settings the tool runs with. Required: client identifier, mailbox email. Optional (overridable): IMAP host, IMAP port, network timeout. Fixed (not user-editable here): authority, scopes, token cache location. Sourced from `config.json` merged over program defaults.
- **Authenticated Identity**: The email/account the OAuth token actually represents, obtained from the identity provider after sign-in, used solely to verify against the configured email.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A brand-new user can go from "no configuration" to "successfully listing mail" by editing only `config.json` — with zero edits to installed program files.
- **SC-002**: On first run in a directory without configuration, a usable `config.json` is created and the user is told what to fill in, in a single run, with no crash.
- **SC-003**: 100% of runs with a missing required field, placeholder value, or unparseable file stop before any login attempt and name the offending field/file.
- **SC-004**: 100% of email mismatches are surfaced: interactively as a choice, or non-interactively as a safe abort with guidance — zero silent operations on an unintended account.
- **SC-005**: When configured and authenticated emails match, the run proceeds with zero additional prompts.
- **SC-006**: The entire automated test suite for this feature runs offline (no network) and passes.

## Assumptions

- This feature depends on and complements the crash-proof I/O feature (001): that feature owns crash-/hang-safe output, the error boundary, and the network timeout bound; this feature provides the configuration home (including the timeout value) and the onboarding/verification flows. Feature 001 can ship first using a code default; this feature externalizes that default.
- "Working directory" means the current directory at invocation, matching the existing relative-path behavior of the token cache; configuration and cache are expected to be co-located there.
- The identity provider exposes the authenticated account's email (e.g., via the cached account record); if it cannot be determined, verification is skipped and the configured email is used, surfaced clearly.
- The client identifier and mailbox email are user-specific but not secrets; nonetheless `config.json` is treated as local-only and excluded from version control. Only the token cache holds credentials.
- Rule externalization (R5) and environment-variable/.env configuration are OUT OF SCOPE; this feature standardizes on `config.json`.
- Moving the user-editable settings from source into `config.json` supersedes the prior "edit config.py" workflow; project documentation (constitution/handoff/README) will be updated to reflect the new editing point.
