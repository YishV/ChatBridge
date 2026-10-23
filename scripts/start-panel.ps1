#Requires -Version 5.1
<#
.SYNOPSIS
启动本机运行状态和记忆管理面板。
#>
[CmdletBinding()]
param(
    [int]$Port = 8767
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到 .venv，请先运行：uv sync --extra native"
}
Set-Location $Root
Write-Host "[panel] http://127.0.0.1:$Port" -ForegroundColor Cyan
& $python -m wechat_ai_bridge panel --port $Port
