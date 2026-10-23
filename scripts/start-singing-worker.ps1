#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw '找不到 .venv，请先运行：uv sync --extra native'
}
$env:PYTHONPATH = Join-Path $Root 'src'
& $python -m chat_bridge singing-worker
