# 非同期ジョブ（領収書の AI 読取・CSV 解析など）を常駐で実行する（manage.py run_jobs --loop）。
# 本番では systemd のサービス、または cron で `manage.py run_jobs` を 1 分ごとに実行する。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
Set-Location $backend
$env:PYTHONUTF8 = "1"
& $py manage.py run_jobs --loop --interval 2
