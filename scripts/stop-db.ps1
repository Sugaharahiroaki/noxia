# 開発用 PostgreSQL を停止する。
$root = Split-Path -Parent $PSScriptRoot
$pgctl = Join-Path $root ".local\pgsql\bin\pg_ctl.exe"
$data = Join-Path $root ".local\pgdata"
& $pgctl -D $data -m fast stop
