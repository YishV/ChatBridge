#Requires -Version 5.1
<#
.SYNOPSIS
启动本机 GPT-SoVITS api_v2，供 QQ 语音条使用。
#>
[CmdletBinding()]
param([string]$GptHome = '')

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $GptHome) {
    $GptHome = Join-Path $Root 'tools\GPT-SoVITS-v2\GPT-SoVITS-v2-240821'
}
$python = Join-Path $GptHome 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到 GPT-SoVITS runtime：$python"
}

# DSH/代理环境里的 NO_PROXY 可能让旧 httpx 解析失败，API 是纯本机服务。
foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$cache = Join-Path $GptHome 'cache'
New-Item -ItemType Directory -Force -Path (Join-Path $cache 'hf'), (Join-Path $cache 'mpl') | Out-Null
$env:HF_HOME = Join-Path $cache 'hf'
$env:TRANSFORMERS_CACHE = $env:HF_HOME
$env:MPLCONFIGDIR = Join-Path $cache 'mpl'
Set-Location $GptHome

Write-Host "[gpt-sovits] $GptHome" -ForegroundColor Cyan
& $python api_v2.py -a 127.0.0.1 -p 9880
