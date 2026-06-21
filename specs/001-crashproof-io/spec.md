# Feature Specification: Crash-proof Unicode I/O & Resilient Header Decoding

**Feature Branch**: `001-crashproof-io`

**Created**: 2026-06-21

**Status**: Draft

**Input**: User description: "開始修正編碼問題，這個最嚴重，勢必以相容各種主機平台的考量來徹底解決此問題。_decode() 小瑕疵請一起修正，email 是全球收信，勢必會遇到各種語文的問題，必須盡可能相容處理，如果真的不行遇到異常，請採用優雅形式去處理完成用戶的動作，絕對不允許任何程式已知的意外而崩潰 crash & stuck。"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mail listing never crashes on output encoding (Priority: P1)

A user runs MailKeeper on their own machine. Their console or output target may use any encoding (a legacy Windows code page, UTF-8, or a redirected file/pipe). Their inbox contains subjects and senders in many languages plus emoji. The user expects to see the full listing and the rule dry-run results, and expects the program to finish — never to abort partway with an encoding error.

**Why this priority**: This is the most severe defect today. The tool currently raises `UnicodeEncodeError` on the first non-representable character when output is not UTF-8, aborting the user's action entirely. Without this, the tool is unusable on common Windows setups and whenever output is redirected.

**Independent Test**: Run the listing with output directed to a simulated non-Unicode target containing CJK/emoji/Korean subjects; confirm every line is emitted (with a visible placeholder only where the target genuinely cannot represent a character) and the program exits successfully with no crash.

**Acceptance Scenarios**:

1. **Given** an inbox with subjects in CJK, Korean, and emoji, **When** output is sent to a non-Unicode target, **Then** all listing lines are emitted (placeholders where unrepresentable) and the program completes successfully.
2. **Given** a Unicode-capable output target, **When** the listing is produced, **Then** all characters render correctly with no unnecessary placeholders.
3. **Given** the same run, **When** the rule dry-run section prints, **Then** it is subject to the same guarantee (no encoding crash).

---

### User Story 2 - Worldwide header decoding is best-effort and never fails (Priority: P1)

A user receives mail from senders around the world, whose subject and sender headers use a wide range of encodings and MIME encoded-word forms — including headers folded across multiple lines and legacy byte sequences with no declared character set. The user expects these to be shown as readable text wherever possible, and — when a header genuinely cannot be decoded — to see a graceful best-effort or placeholder result rather than raw `=?...?=` syntax or an aborted run.

**Why this priority**: Global mail guarantees a steady stream of diverse encodings. Decoding that throws would crash the run (violating the never-crash mandate), and decoding that silently surfaces raw encoded-words degrades the core value (readable subjects). This underpins the tool's primary purpose: listing readable mail.

**Independent Test**: Feed a corpus of header samples (UTF-8, Big5, GBK/GB2312, ISO-2022-JP, EUC-KR, folded multi-segment, undeclared/mojibake, malformed bytes, empty, and absent) to the decoder offline; confirm it never raises, always returns a string, decodes standard encoded-words to readable text, and degrades gracefully for the undecodable cases.

**Acceptance Scenarios**:

1. **Given** a subject encoded as a folded multi-line `=?gb2312?...?=` sequence, **When** it is decoded, **Then** the result is readable text, not the raw encoded-word.
2. **Given** a header whose declared character set is unknown or invalid, **When** it is decoded, **Then** a best-effort readable result or a graceful placeholder is returned and no exception is raised.
3. **Given** an empty or absent header, **When** it is decoded, **Then** an empty string is returned without error.
4. **Given** a sender header that arrived as already-mangled bytes with no declared charset, **When** it is decoded, **Then** the decoder attempts recovery and, if confidence is low, leaves the value no worse than received — never raising.

---

### User Story 3 - Graceful failure with no indefinite hang (Priority: P2)

A user runs MailKeeper when something goes wrong: authentication fails, the mailbox is unreachable, the network stalls, or the IMAP server accepts a connection but never responds. The user expects a short, clear explanation and a clean non-zero exit — never a raw stack trace, and never an indefinite hang.

**Why this priority**: The never-crash/never-stuck mandate extends beyond encoding to all anticipated failure paths. A raw traceback is a crash from the user's perspective; an unbounded wait is "stuck." Both must be replaced with bounded, legible failure.

**Independent Test**: Inject failures (authentication error, IMAP error, and a simulated unresponsive connection) via a fake backend offline; confirm each yields a concise message and a non-zero exit with no stack trace, and that a time-bound is enforced for network waits.

**Acceptance Scenarios**:

1. **Given** the IMAP server does not respond, **When** the configured timeout elapses, **Then** the tool reports the timeout clearly and exits non-zero within a bounded time.
2. **Given** authentication fails, **When** the failure occurs, **Then** the tool shows a concise message (no stack trace) and exits non-zero.
3. **Given** an unexpected error of any kind, **When** it propagates to the program boundary, **Then** a short message is shown instead of a raw traceback, while still signalling failure via exit status.
4. **Given** the interactive login wait, **When** the user never completes it, **Then** the wait is bounded and the elapse is reported clearly.

---

### Edge Cases

- Output redirected to a file whose encoding cannot represent some characters in the data.
- A subject containing emoji or rare CJK not present in the active console code page.
- A folded multi-line encoded-word split mid-sequence across header lines.
- A header declaring a character set that the runtime does not recognise.
- A header that is already mojibake (mis-decoded upstream) with no recoverable declaration.
- Empty, whitespace-only, or absent subject/sender.
- An IMAP server that completes the TCP/TLS handshake but never answers a command (black hole) → must time out.
- A device-code login the user never finishes → bounded wait, clear message.
- The silent token-refresh path (no interactive prompt) must remain unaffected and must not be slowed by new bounds.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: All user-facing output MUST be emitted without raising an encoding error, regardless of the host's active output encoding or locale, and regardless of whether output is to a console or redirected to a file/pipe.
- **FR-002**: When a character cannot be represented by the active output target, the system MUST substitute a visible, non-fatal placeholder and continue, rather than abort.
- **FR-003**: When the output target supports Unicode, all characters MUST render correctly with no unnecessary placeholder substitution.
- **FR-004**: The header decoder MUST return readable text for standard MIME encoded-word headers across common world encodings (including at least UTF-8, Big5, GBK/GB2312, ISO-2022-JP, and EUC-KR), including headers folded across multiple lines or segments.
- **FR-005**: For headers whose character set is undeclared or invalid, the decoder MUST attempt a best-effort recovery and MUST degrade to a best-effort or placeholder result rather than raise an exception or surface raw encoded-word syntax.
- **FR-006**: The header decoder MUST NOT raise an exception for any input — including empty, absent, or malformed bytes — and MUST always return a string.
- **FR-007**: Anticipated failures (authentication failure, mailbox/IMAP errors, network errors, timeouts, and configuration errors) MUST produce a concise human-readable message and a non-zero exit status, never a raw stack trace.
- **FR-008**: Unexpected errors MUST also be caught at the program boundary and reported concisely (no raw stack trace) while still signalling failure via a non-zero exit status.
- **FR-009**: Network operations (IMAP connection and reads) MUST be bounded by a configurable timeout, defaulting to 60 seconds, so the tool fails fast instead of blocking indefinitely.
- **FR-010**: The interactive login wait MUST be bounded so it cannot block indefinitely, and MUST report clearly when the bound is reached.
- **FR-011**: The above guarantees MUST hold across the supported host platforms and output targets: non-Unicode Windows consoles, Unicode consoles, and redirected/piped output.
- **FR-012**: These changes MUST preserve the existing backend isolation contract — no change to the `MailBackend` interface or the `MailHeader` domain type, and no IMAP/protocol detail leaking above the seam.
- **FR-013**: No destructive behavior is introduced; existing read-only listing and dry-run-by-default semantics MUST be unaffected.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Running the tool against an inbox containing characters from at least five different scripts/encodings completes with a success exit status and zero encoding crashes, on both a Unicode and a non-Unicode output target.
- **SC-002**: For a representative corpus of header samples, 100% decode without raising; every standard encoded-word sample (declared valid charset, including folded) renders as readable text rather than raw `=?...?=`; every undeclared/malformed sample yields a best-effort or placeholder result with no exception.
- **SC-003**: Every anticipated failure path produces a message of at most a few lines and a non-zero exit, with zero raw stack traces shown to the user.
- **SC-004**: No invocation hangs beyond the configured timeout; a non-responsive ("black-holed") connection aborts within the timeout plus a small margin.
- **SC-005**: The entire automated test suite for this feature runs offline (no network) and passes.

## Assumptions

- The OAuth/token acquisition layer and the `MailBackend`/`MailHeader` contract are unchanged; this feature hardens only presentation, header decoding, and failure/timeout handling.
- A character-set detection library (charset-normalizer) may be added as a project dependency to improve recovery of undeclared/legacy byte sequences. This changes the constitution's locked stack (currently "msal + stdlib") and therefore REQUIRES a constitution amendment as part of this work. (Note: this library is commonly already present transitively via the existing MSAL dependency chain.)
- Automatic reconnect, token-expiry retry loops, and large-mailbox performance/pagination are OUT OF SCOPE and are deferred to roadmap item R7.
- The 60-second network timeout is a configurable default; the mechanism for user configuration is owned by the separate configuration feature (R4) and is not introduced here — this feature only ensures a finite, overridable bound exists.
- The primary platform risk addressed is Windows non-Unicode consoles and redirected output; the same guarantees are expected to hold on POSIX hosts.
- "Graceful placeholder" means a visible, information-preserving substitution (so the user still sees that a value was present) rather than silent omission.
