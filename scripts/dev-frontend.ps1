<#
.SYNOPSIS
  Next.js を起動する（http://localhost:3000、同じ LAN からは http://<PCのIP>:3000）。/api は Django(127.0.0.1:8000) へプロキシされる。
.PARAMETER Prod
  本番ビルド（next build → next start）で起動する。iPad で動作確認するときはこちらが速く安定する。
  指定しない場合は開発モード（next dev、ファイル変更が即反映）。
#>
param([switch]$Prod)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root "frontend"
Set-Location $frontend
if (-not (Test-Path (Join-Path $frontend "node_modules"))) { npm install }
if ($Prod) {
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "ビルドに失敗しました。" }
  npm run start
} else {
  npm run dev
}
