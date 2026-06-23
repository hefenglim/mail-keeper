#!/usr/bin/env pwsh
# 突變測試：對核心模組注入人工 bug，量測試套件殺得掉幾成（mutation score）。
# 把「我的測試可不可信」從感覺變成一個數字。慢（數分鐘起跳），非每次必跑；
# 改動 imap_client / classifier 等高風險模組後、或發大版前跑一次。
#
# 前置：pip install -e ".[test]" ; pip install mutmut
# 用法：pwsh scripts/mutation.ps1            # 全跑
#       pwsh scripts/mutation.ps1 results    # 看結果
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"

if ($args.Count -ge 1 -and $args[0] -eq "results") {
    mutmut results
    return
}

if (-not (Get-Command mutmut -ErrorAction SilentlyContinue)) {
    throw "未安裝 mutmut。先執行： python -m pip install mutmut"
}

# 優先針對「seam 以下、最高風險」的後端協定模組；其次規則引擎。
mutmut run `
    --paths-to-mutate "src/mailkeeper/imap_client.py,src/mailkeeper/classifier.py,src/mailkeeper/csv_io.py,src/mailkeeper/progress.py" `
    --runner "python -m pytest -x -q"

Write-Host "`n存活的突變（survived）= 測試沒抓到的注入 bug，逐一檢視並補測試：" -ForegroundColor Yellow
mutmut results
