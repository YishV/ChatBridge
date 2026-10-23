#Requires -Version 5.1
<#
.SYNOPSIS
启动微信或 QQ 网关；QQ 模式会尝试先拉起 NapCat。
.EXAMPLE
.\scripts\start.ps1 -Target qq
.EXAMPLE
.\scripts\start.ps1 -Target all -NapCatHome 'tools\NapCat.Shell'
#>
[CmdletBinding()]
param(
    [ValidateSet('qq', 'wechat', 'all')]
    [string]$Target = 'qq',
    [string]$NapCatHome = '',
    [int]$NapCatTimeoutSeconds = 45,
    [switch]$NoWait
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step([string]$Text) {
    Write-Host "[start] $Text" -ForegroundColor Cyan
}

function Read-EnvMap([string]$Path) {
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $index = $trimmed.IndexOf('=')
        if ($index -lt 1) { continue }
        $key = $trimmed.Substring(0, $index).Trim()
        $value = $trimmed.Substring($index + 1).Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $map[$key] = $value
    }
    return $map
}

function Get-EnvValue($Map, [string]$Key, [string]$Default = '') {
    if ($Map.ContainsKey($Key) -and $Map[$Key]) { return [string]$Map[$Key] }
    return $Default
}

function Test-OneBot([string]$ApiUrl, [string]$Token) {
    try {
        $headers = @{}
        if ($Token) { $headers['Authorization'] = "Bearer $Token" }
        $uri = $ApiUrl.TrimEnd('/') + '/get_login_info'
        $response = Invoke-RestMethod -Uri $uri -Method Post -Headers $headers `
            -ContentType 'application/json' -Body '{}' -TimeoutSec 3
        return ($response.status -eq 'ok')
    } catch {
        return $false
    }
}

$envMap = Read-EnvMap (Join-Path $Root '.env')
if (-not (Test-Path -LiteralPath (Join-Path $Root '.env'))) {
    throw "缺少 .env，请先复制 .env.example 并填写配置。"
}

$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到 .venv，请先运行：uv sync --extra native"
}
$env:PYTHONPATH = Join-Path $Root 'src'

$apiUrl = Get-EnvValue $envMap 'QQ_ONEBOT_API_URL' 'http://127.0.0.1:3000'
$oneBotToken = Get-EnvValue $envMap 'QQ_ONEBOT_TOKEN'
$webhookPort = [int](Get-EnvValue $envMap 'QQ_WEBHOOK_PORT' '8081')
$defaultNapCat = Get-EnvValue $envMap 'NAPCAT_HOME' (Join-Path $Root 'tools\NapCat.Shell')
if (-not $NapCatHome) { $NapCatHome = $defaultNapCat }
$voiceEnabled = (Get-EnvValue $envMap 'QQ_VOICE_ENABLED' 'false').ToLower() -eq 'true'
$gptHome = Get-EnvValue $envMap 'GPT_SOVITS_HOME' ''
if (-not $gptHome) {
    $candidates = Get-ChildItem -LiteralPath (Join-Path $Root 'tools\GPT-SoVITS-v2') `
        -Directory -Filter 'GPT-SoVITS-*' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending
    if ($candidates) { $gptHome = $candidates[0].FullName }
}
$gptLauncher = Join-Path $PSScriptRoot 'start-gpt-sovits.ps1'

function Start-GptSoVits {
    if (-not $voiceEnabled) {
        Write-Step 'QQ 语音未启用，跳过 GPT-SoVITS'
        return
    }
    if (-not $gptHome -or -not (Test-Path -LiteralPath (Join-Path $gptHome 'runtime\python.exe'))) {
        Write-Warning '未找到 GPT-SoVITS 运行时；请设置 GPT_SOVITS_HOME 或手动启动 api_v2.py。'
        return
    }
    if (Get-NetTCPConnection -State Listen -LocalPort 9880 -ErrorAction SilentlyContinue) {
        Write-Step 'GPT-SoVITS 已在 9880 运行'
        return
    }
    if (-not (Test-Path -LiteralPath $gptLauncher)) {
        Write-Warning "缺少 $gptLauncher；跳过 GPT-SoVITS。"
        return
    }
    Write-Step '启动 GPT-SoVITS API（独立窗口）'
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $gptLauncher, '-GptHome', $gptHome
    ) -WorkingDirectory $gptHome
    if ($NoWait) { return }
    for ($i = 0; $i -lt 60; $i += 2) {
        if (Get-NetTCPConnection -State Listen -LocalPort 9880 -ErrorAction SilentlyContinue) {
            Write-Step 'GPT-SoVITS API 已就绪'
            return
        }
        Start-Sleep -Seconds 2
    }
    throw 'GPT-SoVITS API 在 60 秒内未就绪；请查看独立窗口。'
}

function Start-NapCatIfNeeded {
    $running = Get-Process -Name 'NapCatWinBootMain' -ErrorAction SilentlyContinue
    if (-not $running) {
        $launcher = Join-Path $NapCatHome 'launcher-user.bat'
        if (-not (Test-Path -LiteralPath $launcher)) {
            Write-Warning "未找到 NapCat 启动器：$launcher；跳过拉起 NapCat。"
            return
        }
        Write-Step "启动 NapCat：$NapCatHome"
        # QQ 的富媒体上传不走本机代理；继承 127.0.0.1 代理会导致语音/图片发送报
        # rich media transfer failed。只对 NapCat 子进程清空代理，不改变网关进程。
        $proxyNames = @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                        'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
        $savedProxy = @{}
        foreach ($name in $proxyNames) {
            $savedProxy[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        }
        try {
            Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', "`"$launcher`"") -WorkingDirectory $NapCatHome
        } finally {
            foreach ($name in $proxyNames) {
                if ($null -ne $savedProxy[$name]) {
                    Set-Item -Path "Env:$name" -Value $savedProxy[$name]
                }
            }
        }
    } else {
        Write-Step 'NapCat 已在运行'
    }

    if ($NoWait) { return }
    Write-Step '等待 NapCat OneBot API（需要登录时请先扫码）...'
    for ($i = 0; $i -lt $NapCatTimeoutSeconds; $i += 2) {
        if (Test-OneBot $apiUrl $oneBotToken) {
            Write-Step 'NapCat OneBot API 已就绪'
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "NapCat OneBot API 在 $NapCatTimeoutSeconds 秒内未就绪；请登录 NapCat 后重新运行。"
}

function Start-SingingWorker {
    $enabled = (Get-EnvValue $envMap 'SINGING_ENABLED' 'false').ToLower() -eq 'true'
    if (-not $enabled) { return }
    Write-Step '启动翻唱 worker（独立窗口）'
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoExit', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $Root 'scripts\start-singing-worker.ps1')
    ) -WorkingDirectory $Root
}

function Start-QQ {
    if (Get-NetTCPConnection -State Listen -LocalPort $webhookPort -ErrorAction SilentlyContinue) {
        Write-Warning "端口 $webhookPort 已有监听，QQ 网关可能已经启动。"
        return
    }
    Start-GptSoVits
    Start-NapCatIfNeeded
    Start-SingingWorker
    Write-Step '启动 QQ 网关；按 Ctrl+C 停止'
    & $python -m wechat_ai_bridge qq run
}

function Start-WeChat {
    Write-Step '启动微信网关；按 Ctrl+C 停止'
    & $python -m wechat_ai_bridge run
}

if ($Target -eq 'all') {
    $common = @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-NapCatHome', $NapCatHome)
    Start-Process -FilePath 'powershell.exe' -ArgumentList ($common + @('-Target', 'qq')) -WorkingDirectory $Root
    Start-Process -FilePath 'powershell.exe' -ArgumentList ($common + @('-Target', 'wechat')) -WorkingDirectory $Root
    Write-Step '已打开 QQ 和微信两个窗口'
    return
}

if ($Target -eq 'qq') {
    Start-QQ
} else {
    Start-WeChat
}
