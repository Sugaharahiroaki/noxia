<#
.SYNOPSIS
  検証用の環境（DB: yorumiru_verify、Django 8001、ジョブ、Next 3001）を作って起動する。E2E（.local/e2e/*.mjs）はこの環境に対して実行する。
.DESCRIPTION
  開発用 DB（yorumiru）を壊さずに、マイグレーション・seed・画面の動作を確かめるための環境。
    -Reset  検証用 DB を作り直して migrate → seed_dev → seed_tax_config を行う（初回は必須）
    -Stop   検証用の Django・ジョブ・Next を止める
  例:
    powershell -ExecutionPolicy Bypass -File scripts\verify-env.ps1 -Reset
    cd .local\e2e; $env:BASE_URL="http://localhost:3001"; node receipts.mjs
    powershell -ExecutionPolicy Bypass -File scripts\verify-env.ps1 -Stop
#>
param([switch]$Reset, [switch]$Stop)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$psql = Join-Path $root ".local\pgsql\bin\psql.exe"
$logs = Join-Path $root ".local"
$db = "yorumiru_verify"

function Stop-Verify {
  # 自分の PowerShell を巻き込まないよう、python.exe / node.exe だけをコマンドラインで照合する
  $targets = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'node.exe'" |
    Where-Object { $_.CommandLine -like '*runserver 127.0.0.1:8001*' -or $_.CommandLine -like '*run_jobs --loop*' -or $_.CommandLine -like '*next dev -p 3001*' -or $_.CommandLine -like '*next*dev*3001*' }
  foreach ($p in $targets) { Write-Host "停止: pid=$($p.ProcessId) $($p.Name)"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
}

if ($Stop) { Stop-Verify; Write-Host "検証用サーバーを止めました。"; exit 0 }

Stop-Verify
if ($Reset) {
  $env:PGPASSWORD = "yorumiru"
  & $psql -h 127.0.0.1 -U yorumiru -d postgres -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db" | Out-Null
  $env:PYTHONUTF8 = "1"; $env:POSTGRES_DB = $db
  Set-Location $backend
  & $py manage.py migrate
  & $py manage.py seed_dev
  & $py manage.py seed_tax_config
  Remove-Item Env:POSTGRES_DB
}

Remove-Item (Join-Path $logs "verify-*.log") -Force -ErrorAction SilentlyContinue
$django = "`$env:PYTHONUTF8='1'; `$env:POSTGRES_DB='$db'; `$env:CSRF_TRUSTED_ORIGINS='http://localhost:3001'; Set-Location '$backend'; & '$py' manage.py runserver 127.0.0.1:8001 --noreload"
Start-Process powershell -ArgumentList "-NoProfile", "-Command", $django -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "verify-django.log") -RedirectStandardError (Join-Path $logs "verify-django.err.log")
$jobs = "`$env:PYTHONUTF8='1'; `$env:POSTGRES_DB='$db'; Set-Location '$backend'; & '$py' manage.py run_jobs --loop --interval 2"
Start-Process powershell -ArgumentList "-NoProfile", "-Command", $jobs -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "verify-jobs.log") -RedirectStandardError (Join-Path $logs "verify-jobs.err.log")
$next = "`$env:BACKEND_URL='http://127.0.0.1:8001'; Set-Location '$frontend'; npx next dev -p 3001"
Start-Process powershell -ArgumentList "-NoProfile", "-Command", $next -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs "verify-next.log") -RedirectStandardError (Join-Path $logs "verify-next.err.log")

Write-Host "起動しました: Django http://127.0.0.1:8001（DB $db）、ジョブ、Next http://localhost:3001（初回は数十秒かかります）"
Write-Host "ログ: .local\verify-django.err.log / verify-jobs.log / verify-next.log。止めるときは -Stop。"
