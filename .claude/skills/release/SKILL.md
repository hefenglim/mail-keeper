---
name: "release"
description: "Use when cutting and publishing a new MailKeeper version — a feature is merged to main and you need to ship a release (version bump, package validation, tag, GitHub Release)."
compatibility: "MailKeeper repo: requires pyproject.toml (src layout), scripts/verify-package.ps1, and .github/workflows/release.yml"
metadata:
  author: "mailkeeper"
  source: "codified from the project's established release workflow"
user-invocable: true
disable-model-invocation: false
---

# MailKeeper Release

## Overview

The orthodox MailKeeper release flow: **bump → verify in a clean env → land on main → tag → CI publishes**.
A pushed semver tag `vX.Y.Z` triggers `.github/workflows/release.yml`, which builds, validates, installs
on a clean cross-platform matrix, and publishes a GitHub Release with the wheel/sdist and a copy-paste
install command. **Never publish a build you have not verified with full dependency resolution.**

## Preconditions (gate — do not release until all true)

- The feature is merged to `main` and local `main` is synced (`git checkout main && git pull`).
- Definition of Done met: tests green offline, `mypy` clean, Senior Review = APPROVE.
- Working tree clean; you are not on a feature branch.

## Procedure

1. **Pick the version** (semver) consistent with the pending `CHANGELOG.md` entry.
2. **Bump the version in BOTH places (must match):**
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `src/mailkeeper/__init__.py` → `__version__ = "X.Y.Z"`
   - Add a `CHANGELOG.md` entry `## [X.Y.Z] - <REAL today's date>` (the actual delivery date — never fabricated).
3. **Local pre-flight — authoritative package check:**
   ```powershell
   pwsh scripts/verify-package.ps1
   ```
   Must end with `PACKAGE VERIFIED -> mailkeeper-X.Y.Z-...whl`. This builds, runs `twine check`, scans the
   wheel for secrets, installs into a throwaway venv **with full dependency resolution** (not `--no-deps`),
   and smoke-tests. If it fails, fix before tagging.
4. **Land the bump on `main`** via a chore branch + PR (project rule: one branch per change):
   ```bash
   git checkout -b chore/release-vX.Y.Z
   git add pyproject.toml src/mailkeeper/__init__.py CHANGELOG.md
   git commit            # imperative subject; include the Claude-Session trailer
   git push -u origin chore/release-vX.Y.Z
   gh pr create --base main --fill
   gh pr merge --merge --delete-branch
   git checkout main && git pull
   ```
5. **Confirm tag == package version**: the tag you are about to push MUST equal the `pyproject.toml` version
   (`vX.Y.Z` ↔ `X.Y.Z`). A mismatched tag publishes a mislabeled release.
6. **Tag and push — this triggers the release CI:**
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
7. **Watch CI to completion:**
   ```bash
   gh run watch <run-id> --exit-status
   ```
   It runs build → `twine check` → no-secret scan → install+smoke on ubuntu/windows × Python 3.10/3.13 →
   publishes the GitHub Release.
8. **Verify the published Release:**
   ```bash
   gh release view vX.Y.Z
   ```
   Assets include `mailkeeper-X.Y.Z-py3-none-any.whl` + `.tar.gz`, and the body shows the version-filled,
   copy-paste `pip install` command.
9. **Sync local** if anything changed: `git checkout main && git pull`.

## Invariants (never violate)

- **Secrets never ship.** `token_cache.bin`, `config.json`, `*Client ID*.txt`, `released/` stay gitignored;
  the CI wheel scan is the backstop, not a substitute for not staging them.
- **Honest CHANGELOG.** The date is the real delivery date (constitution §2).
- **Versions agree.** `pyproject.toml` version == `__init__.__version__` == git tag.
- **Never commit** `dist/`, `build/`, `*.egg-info/`, `released/`.

## Quick reference

| Step | Command |
|------|---------|
| Pre-flight verify | `pwsh scripts/verify-package.ps1` |
| Tag + trigger CI | `git tag vX.Y.Z && git push origin vX.Y.Z` |
| Watch CI | `gh run watch <run-id> --exit-status` |
| Inspect release | `gh release view vX.Y.Z` |

## Common mistakes

- Tagging before `verify-package.ps1` passes → a broken artifact gets published.
- Bumping only one of `pyproject.toml` / `__init__.py` → version mismatch.
- Using `pip install --no-deps --force-reinstall` as the "test" → hides missing dependencies; use the venv verify.
- Pushing the tag before the version bump is on `main` → the release builds the wrong version.

## Optional enhancements (not yet wired)

- **CI tag↔version guard**: add a step in `release.yml` failing the run if `${GITHUB_REF_NAME#v}` ≠ the
  `pyproject.toml` version — turns invariant 3 into an automated gate.
- **PyPI Trusted Publishing (OIDC)**: if public `pip install mailkeeper` (no URL) is ever wanted.
