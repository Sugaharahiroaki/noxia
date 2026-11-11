# 開発用 PostgreSQL を起動する（.local/pgdata）。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pg = Join-Path $root ".local\pgsql"
$data = Join-Path $root ".local\pgdata"
$log = Join-Path $root ".local\pg.log"
$pgctl = Join-Path $pg "bin\pg_ctl.exe"
if (-not (Test-Path $pgctl)) { throw "PostgreSQL が見つかりません。先に scripts\setup-dev-db.ps1 を実行してください。" }
& $pgctl -D $data status *> $null
if ($LASTEXITCODE -eq 0) { Write-Host "PostgreSQL は起動済みです。"; exit 0 }
# ハンドルを引き継がないよう別プロセスで起動する
Start-Process -FilePath $pgctl -ArgumentList @("-D", "`"$data`"", "-l", "`"$log`"", "-w", "start") -NoNewWindow -Wait
& $pgctl -D $data status
