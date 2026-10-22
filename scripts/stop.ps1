#Requires -Version 5.1
<#
.SYNOPSIS
停止 QQ/微信网关及本地翻唱、GPT-SoVITS、NapCat 进程。
.DESCRIPTION
只按命令行和端口匹配，不结束所有 python.exe。
.EXAMPLE
.\scripts\stop.ps1 -Target qq
.EXAMPLE
.\scripts\stop.ps1 -Target all
#>
[CmdletBinding()]
param(
    [ValidateSet('qq', 'wechat', 'singing', 'all')]
    [string]$Target = 'qq'
)

$ErrorActionPreference = 'SilentlyContinue'

function Write-Step([string]$Text) {
    Write-Host "[stop] $Text" -ForegroundColor Cyan
}

function Find-PythonMatching([string]$Pattern) {
    @(Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^(python|pythonw)\.exe$' -and
            $_.CommandLine -and
            $_.CommandLine -match $Pattern
        })
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" |
        Select-Object -ExpandProperty ProcessId)
    foreach ($childId in $children) {
        Stop-ProcessTree -ProcessId ([int]$childId)
    }
    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
        Write-Host "  stopped PID $ProcessId"
    } catch {
        # Process may already be gone due to a parent tree stop.
    }
}

function Stop-Matching([string]$Label, [string]$Pattern) {
    $processes = @(Find-PythonMatching $Pattern)
    if ($processes.Count -eq 0) {
        Write-Step "$Label：没有运行"
        return
    }
    $ids = @($processes | Select-Object -ExpandProperty ProcessId -Unique)
    Write-Step "$Label：停止 $($ids -join ', ')"
    foreach ($processId in $ids) {
        Stop-ProcessTree -ProcessId ([int]$processId)
    }
}

function Stop-Listener([int]$Port, [string]$Label) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($listeners.Count -eq 0) {
        Write-Step "$Label（端口 $Port）：没有监听"
        return
    }
    Write-Step "$Label（端口 $Port）：停止 $($listeners -join ', ')"
    foreach ($processId in $listeners) {
        Stop-ProcessTree -ProcessId ([int]$processId)
    }
}

function Stop-QQ {
    Stop-Matching 'QQ 网关' 'wechat_ai_bridge\s+qq\s+run'
    Stop-Matching '翻唱 worker' 'wechat_ai_bridge\s+singing-worker'
    Stop-Listener 8081 'QQ webhook'
    Stop-Matching 'GPT-SoVITS API' 'api_v2\.py'
    Stop-Listener 9880 'GPT-SoVITS'
    $napcat = @(Get-CimInstance Win32_Process |
        Where-Object { $_.Name -match '^NapCatWinBootMain(\.exe)?$' } |
        Select-Object -ExpandProperty ProcessId -Unique)
    if ($napcat.Count -eq 0) {
        Write-Step 'NapCat：没有运行'
    } else {
        Write-Step "NapCat：停止 $($napcat -join ', ')"
        foreach ($processId in $napcat) {
            Stop-ProcessTree -ProcessId ([int]$processId)
        }
    }
    Stop-Listener 3000 'NapCat OneBot'
}

function Stop-WeChat {
    Stop-Matching '微信网关' 'wechat_ai_bridge\s+run(\s|$)'
}

function Show-Ports {
    Start-Sleep -Seconds 1
    foreach ($port in 3000, 8081, 9880) {
        $listening = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        if ($listening.Count -eq 0) {
            Write-Host "  port ${port}: free"
        } else {
            Write-Host "  port ${port}: still listening" -ForegroundColor Yellow
        }
    }
}

if ($Target -eq 'qq') {
    Stop-QQ
} elseif ($Target -eq 'wechat') {
    Stop-WeChat
} elseif ($Target -eq 'singing') {
    Stop-Matching '翻唱 worker' 'wechat_ai_bridge\s+singing-worker'
} else {
    Stop-QQ
    Stop-WeChat
}
Show-Ports
