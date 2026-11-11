<#
.SYNOPSIS
  pg_dump（カスタム形式）からの復元。既存データは上書きされる（--clean）。
.PARAMETER File   復元するダンプファイル
.PARAMETER DbUrl  接続先（既定 開発用DB）
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 -File backups\yorumiru-20261024-120000.dump
#>
param(
  [Parameter(Mandatory = $true)][string]$File,
  [string]$DbUrl = "postgresql://yorumiru:yorumiru@localhost:5432/yorumiru"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pgrestore = Join-Path $root ".local\pgsql\bin\pg_restore.exe"
if (-not (Test-Path $pgrestore)) { $pgrestore = "pg_restore" }
if (-not (Test-Path $File)) { throw "ファイルが見つかりません: $File" }
Write-Host "復元先: $DbUrl"
Write-Host "既存のデータは削除されます。続行するには 'yes' と入力してください。"
$ans = Read-Host
if ($ans -ne "yes") { Write-Host "中止しました。"; exit 1 }
& $pgrestore --clean --if-exists --no-owner --no-privileges --dbname=$DbUrl $File
Write-Host "復元が完了しました。アプリを再起動してください。"
