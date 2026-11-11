# Django 開発サーバーを起動する（http://127.0.0.1:8000）。初回は venv 作成・依存インストール・migrate を行う。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "venv を作成しています..."
  python -m venv (Join-Path $backend ".venv")
  & $py -m pip install --upgrade pip
  & $py -m pip install -r (Join-Path $backend "requirements.txt")
}
if (-not (Test-Path (Join-Path $backend ".env"))) { Copy-Item (Join-Path $backend ".env.example") (Join-Path $backend ".env") }
Set-Location $backend
$env:PYTHONUTF8 = "1"
& $py manage.py migrate
& $py manage.py runserver 127.0.0.1:8000
