<#
.SYNOPSIS
  PostgreSQL のバックアップ（pg_dump カスタム形式）。
.PARAMETER OutDir   出力先（既定 backups/）
.PARAMETER DbUrl    接続先（既定 postgresql://yorumiru:yorumiru@localhost:5432/yorumiru）
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1
  powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1 -DbUrl "postgresql://user:pass@host:5432/db"
#>
param(
  [string]$OutDir = "",
  [string]$DbUrl = "postgresql://yorumiru:yorumiru@localhost:5432/yorumiru"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $root "backups" }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$pgdump = Join-Path $root ".local\pgsql\bin\pg_dump.exe"
if (-not (Test-Path $pgdump)) { $pgdump = "pg_dump" }  # PATH 上の pg_dump を使う
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$file = Join-Path $OutDir "yorumiru-$stamp.dump"
& $pgdump --format=custom --no-owner --no-privileges --file=$file $DbUrl
if ($LASTEXITCODE -ne 0) { throw "pg_dump に失敗しました" }
Write-Host "バックアップを作成しました: $file"
Write-Host "復元: scripts\restore-db.ps1 -File `"$file`""

# 原本ファイル（領収書・契約書・出力した申告書類）も同じタイミングで保存する。
# 電子帳簿保存法の原本は DB だけ戻しても復元できないため、DB と対にして保管する。暗号鍵（FIELD_ENCRYPTION_KEY_*）は別の場所に保管すること。
$mediaRoot = $env:MEDIA_ROOT
if (-not $mediaRoot) { $mediaRoot = Join-Path $root "backend\media" }
if (Test-Path $mediaRoot) {
  $mediaZip = Join-Path $OutDir "yorumiru-media-$stamp.zip"
  Compress-Archive -Path (Join-Path $mediaRoot "*") -DestinationPath $mediaZip -Force
  Write-Host "原本ファイルのバックアップを作成しました: $mediaZip"
} else {
  Write-Host "原本ファイルの保存先 $mediaRoot が無いため、ファイルのバックアップは省きました"
}
