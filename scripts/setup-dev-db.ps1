<#
.SYNOPSIS
  開発用 PostgreSQL（ポータブル版・管理者権限不要）をプロジェクト内に用意する。

  1. EnterpriseDB のバイナリ zip をダウンロード（.local/downloads）
  2. .local/pgsql に展開、.local/pgdata を initdb（スーパーユーザー postgres）
  3. localhost:5432 で起動し、ロール yorumiru / DB yorumiru を作成

.PARAMETER Version   PostgreSQL のバージョン（既定 17.6-1）
.PARAMETER Port      ポート（既定 5432）
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup-dev-db.ps1
#>
param(
  [string]$Version = "17.6-1",
  [int]$Port = 5432
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$local = Join-Path $root ".local"
$dl = Join-Path $local "downloads"
$pg = Join-Path $local "pgsql"
$data = Join-Path $local "pgdata"
$zip = Join-Path $dl "postgresql-$Version-windows-x64-binaries.zip"
$url = "https://get.enterprisedb.com/postgresql/postgresql-$Version-windows-x64-binaries.zip"

New-Item -ItemType Directory -Force $dl | Out-Null
if (-not (Test-Path (Join-Path $pg "bin\pg_ctl.exe"))) {
  if (-not (Test-Path $zip)) {
    Write-Host "PostgreSQL $Version をダウンロードしています（約330MB）..."
    Invoke-WebRequest -Uri $url -OutFile $zip
  }
  Write-Host "展開しています..."
  Expand-Archive -Path $zip -DestinationPath $local -Force
  Remove-Item -Recurse -Force (Join-Path $pg "pgAdmin 4"), (Join-Path $pg "StackBuilder"), (Join-Path $pg "doc") -ErrorAction SilentlyContinue
}

if (-not (Test-Path (Join-Path $data "PG_VERSION"))) {
  $pwfile = Join-Path $local "pgpass.txt"
  Set-Content -Path $pwfile -Value "yorumiru_dev_pw" -Encoding ascii -NoNewline
  Write-Host "データディレクトリを初期化しています..."
  & (Join-Path $pg "bin\initdb.exe") -D $data -U postgres --pwfile=$pwfile -E UTF8 --locale=C | Out-Null
  Add-Content -Path (Join-Path $data "postgresql.conf") -Value @"

# --- yorumiru dev overrides ---
port = $Port
listen_addresses = 'localhost'
timezone = 'Asia/Tokyo'
log_timezone = 'Asia/Tokyo'
"@
}

& (Join-Path $PSScriptRoot "start-db.ps1")

$env:PGPASSWORD = "yorumiru_dev_pw"
$psql = Join-Path $pg "bin\psql.exe"
$roleExists = & $psql -h localhost -p $Port -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='yorumiru'"
if ($roleExists -ne "1") { & $psql -h localhost -p $Port -U postgres -c "CREATE ROLE yorumiru WITH LOGIN PASSWORD 'yorumiru' CREATEDB;" }
$dbExists = & $psql -h localhost -p $Port -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='yorumiru'"
if ($dbExists -ne "1") { & $psql -h localhost -p $Port -U postgres -c "CREATE DATABASE yorumiru OWNER yorumiru ENCODING 'UTF8';" }
Remove-Item Env:PGPASSWORD
Write-Host "完了: postgresql://yorumiru:yorumiru@localhost:$Port/yorumiru"
