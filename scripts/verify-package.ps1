<#
.SYNOPSIS
    Build and verify the MailKeeper package in an isolated environment (steps 2-6).

.DESCRIPTION
    2) build wheel + sdist
    3) twine check + inspect wheel contents (no secrets packaged, expected modules present)
    4) install the wheel WITH dependencies into a throwaway venv (proves dep metadata)
    5) network-free smoke test (version, imports, `--help`, console entry point)
    6) tear down the venv
    Exits non-zero on any failure, so it is CI / pre-release friendly.

    Authoritative package check — unlike `pip install --no-deps --force-reinstall`,
    this resolves dependencies in a clean env and would catch a missing dep
    (e.g. charset-normalizer not declared).

.PARAMETER PythonExe
    Python executable to drive the build/venv (default: python).

.PARAMETER KeepVenv
    Keep the temporary venv instead of deleting it (for debugging).

.EXAMPLE
    pwsh scripts/verify-package.ps1
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "python",
    [switch]$KeepVenv
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$dist = Join-Path $repo "dist"
$venv = Join-Path $repo ".venv-verify"

function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Fail($m) { Write-Host "FAIL: $m" -ForegroundColor Red; exit 1 }
function CheckExit($m) { if ($LASTEXITCODE -ne 0) { Fail $m } }

# --- Step 2: build ---
Step "2/6 Build wheel + sdist"
& $PythonExe -m pip install --quiet --upgrade build twine; CheckExit "could not install build/twine"
& $PythonExe -m build; CheckExit "python -m build failed"

$wheel = Get-ChildItem "$dist\*.whl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$sdist = Get-ChildItem "$dist\*.tar.gz" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) { Fail "no wheel produced in dist/" }
if (-not $sdist) { Fail "no sdist produced in dist/" }
Write-Host "wheel: $($wheel.Name)"
Write-Host "sdist: $($sdist.Name)"

# --- Step 3: validate the artifact itself ---
Step "3/6 Validate artifact (twine check + contents)"
& $PythonExe -m twine check $wheel.FullName $sdist.FullName; CheckExit "twine check failed"

$contents = & $PythonExe -m zipfile -l $wheel.FullName
$leak = $contents | Select-String -Pattern 'token_cache|config\.json|Client ID|\.env|\.pem|\.key'
if ($leak) { Fail "secret-like file packaged inside the wheel:`n$leak" }

foreach ($m in @(
    "mailkeeper/cli.py", "mailkeeper/csv_io.py", "mailkeeper/classifier.py",
    "mailkeeper/menu.py", "mailkeeper/imap_client.py", "mailkeeper/config_store.py"
)) {
    if (-not ($contents | Select-String -SimpleMatch $m)) { Fail "expected module missing from wheel: $m" }
}
Write-Host "artifact OK: no secrets, all expected modules present"

# --- Step 4: install into a clean venv WITH dependencies ---
Step "4/6 Install into clean venv (full dependency resolution)"
if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
& $PythonExe -m venv $venv; CheckExit "venv creation failed"
$vpy = Join-Path $venv "Scripts\python.exe"
& $vpy -m pip install --quiet --upgrade pip; CheckExit "pip upgrade failed"
# NOTE: no --no-deps — this proves the wheel declares all its dependencies.
& $vpy -m pip install --quiet $wheel.FullName
CheckExit "install (with deps) failed — check pyproject [project].dependencies"

# --- Step 5: network-free smoke test ---
Step "5/6 Smoke test (no network)"
$env:PYTHONUTF8 = "1"
$ver = & $vpy -c "import mailkeeper; print(mailkeeper.__version__)"; CheckExit "version import failed"
Write-Host "installed version: $ver"
& $vpy -c "import mailkeeper.cli, mailkeeper.csv_io, mailkeeper.classifier, mailkeeper.menu, mailkeeper.imap_client, mailkeeper.config_store; print('imports OK')"
CheckExit "module import smoke failed"
& $vpy -m mailkeeper --help | Out-Null; CheckExit "'python -m mailkeeper --help' failed"
& (Join-Path $venv "Scripts\mailkeeper.exe") --help | Out-Null; CheckExit "console entry point 'mailkeeper --help' failed"
Write-Host "smoke OK: imports + console entry point + subcommand help"

# --- Step 6: teardown ---
Step "6/6 Done"
if (-not $KeepVenv) { Remove-Item -Recurse -Force $venv }

Write-Host "`nPACKAGE VERIFIED -> $($wheel.Name) (version $ver)" -ForegroundColor Green
