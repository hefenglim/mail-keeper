#!/usr/bin/env pwsh
# 本機覆蓋率檢查：對齊 CI（.github/workflows/ci.yml）的閘門。
# 用法： pwsh scripts/coverage.ps1
$ErrorActionPreference = "Stop"

$env:PYTHONPATH = "src"

Write-Host "== pytest + coverage (package gate 85%) ==" -ForegroundColor Cyan
python -m pytest --cov=mailkeeper --cov-report=term-missing --cov-fail-under=85
if ($LASTEXITCODE -ne 0) { throw "套件覆蓋率未達 85% 或測試失敗" }

Write-Host "== coverage gate: imap_client (protocol seam) 88% ==" -ForegroundColor Cyan
python -m coverage report --include="*/imap_client.py" --fail-under=88
if ($LASTEXITCODE -ne 0) { throw "imap_client 覆蓋率未達 88%" }

Write-Host "COVERAGE OK" -ForegroundColor Green
