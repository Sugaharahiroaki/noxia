<#
.SYNOPSIS
  開発用に PostgreSQL・Django・Next.js をまとめて起動し、ブラウザを開く。
  Django と Next.js はそれぞれ別ウィンドウで起動する（止めるときはそのウィンドウで Ctrl+C）。
.PARAMETER Dev
  Next.js を開発モード（next dev）で起動する。指定しなければ本番ビルド（速く安定。iPad 確認向け）。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1
#>
param([switch]$Dev)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

function Test-Url($url) {
  try { (Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200 } catch { $false }
}
function Test-Port($port) {
  [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# 1. PostgreSQL
& (Join-Path $here "start-db.ps1")

# 2. Django（別ウィンドウ）
if (Test-Port 8000) {
  Write-Host "ポート 8000 は使用中です（Django 起動済みとみなします）。"
} else {
  Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$(Join-Path $here 'dev-backend.ps1')`"")
}

# 2b. 非同期ジョブ（領収書の AI 読取・CSV 解析。別ウィンドウ）
Start-Process powershell -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$(Join-Path $here 'dev-jobs.ps1')`"")

# 3. Next.js（別ウィンドウ）
if (Test-Port 3000) {
  Write-Host "ポート 3000 は使用中です（Next.js 起動済みとみなします）。"
} else {
  $feArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$(Join-Path $here 'dev-frontend.ps1')`"")
  if (-not $Dev) { $feArgs += "-Prod" }
  Start-Process powershell -ArgumentList $feArgs
}

# 4. 起動待ち
Write-Host "起動を待っています..."
$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) {
  if ((Test-Url "http://127.0.0.1:8000/api/health") -and (Test-Url "http://localhost:3000/login")) { break }
  Start-Sleep -Seconds 2
}
if (-not ((Test-Url "http://127.0.0.1:8000/api/health") -and (Test-Url "http://localhost:3000/login"))) {
  Write-Warning "時間内に起動を確認できませんでした。各ウィンドウのエラーを確認してください。"
  exit 1
}

$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254*" } |
  Select-Object -ExpandProperty IPAddress
Write-Host ""
Write-Host "起動しました。"
Write-Host "  この PC:  http://localhost:3000"
foreach ($ip in $ips) { Write-Host "  iPad など（同じ Wi-Fi）: http://${ip}:3000" }
Write-Host "  開発用ログイン: owner_a / manager_a1 / staff_a1 / cast_a1 など（パスワード Dev-pass-1234）"
Start-Process "http://localhost:3000"
