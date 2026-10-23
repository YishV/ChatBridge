#Requires -Version 5.1
<#
.SYNOPSIS
清理运行时缓存和数据库；默认只预览，不删除。

.DESCRIPTION
默认保护：曲库、表情包、音色/参考音频、配置、凭据、运行环境和知识库。
数据库默认自动备份到 data/backups/cleanup-*，可用 -NoBackup 关闭。

.EXAMPLE
.\scripts\clean.ps1
# 预览将删除的内容

.EXAMPLE
.\scripts\clean.ps1 -Apply
# 清理缓存和聊天/翻唱数据库，但保留长期记忆

.EXAMPLE
.\scripts\clean.ps1 -Apply -IncludeMemory
# 连长期记忆数据库一起清理
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$IncludeMemory,
    [switch]$IncludeDownloads,
    [switch]$NoBackup,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Protected = @(
    'data/music',
    'data/memes',
    'data/voices',
    'data/runtime/memes',
    'data/runtime/gpt-sovits',
    'tools/RVC/assets/weights',
    'tools/GPT-SoVITS-v2',
    'tools/NapCat.Shell',
    'data/runtime/native-hook',
    'data/runtime/reader-env',
    'data/runtime/sandboxes',
    'data/runtime/wechat-4.1.10.27',
    'data/runtime/WeChatAgent-reference',
    'data/runtime/wechatauto-reference',
    'data/runtime/weflow-cli-reference',
    'data/knowledge',
    'config',
    'state/stickers',
    '.env',
    '.env.example'
)

$CacheTargets = @(
    'data/runtime/logs',
    'data/runtime/cover',
    'data/runtime/cover-verification',
    'data/runtime/covers',
    'data/runtime/vision',
    'data/runtime/voice-cache',
    'data/runtime/voice-check',
    'data/runtime/voice-e2e',
    'data/runtime/voice-test',
    'data/runtime/qq-voice',
    'data/runtime/singing',
    '.pytest_cache',
    '.ruff_cache'
)
$CacheTargets += @(Get-ChildItem -Path 'data/runtime' -Directory -Filter 'vision-trigger-*' -ErrorAction SilentlyContinue |
    ForEach-Object { $_.FullName.Substring($Root.Length + 1) })
if ($IncludeDownloads) {
    $CacheTargets += 'data/runtime/downloads'
}

$DatabaseTargets = @(
    'data/runtime/chat-state.sqlite3',
    'data/runtime/qq-chat-state.sqlite3',
    'data/runtime/singing.sqlite3'
)
if ($IncludeMemory) {
    $DatabaseTargets += 'data/runtime/memory.sqlite3'
}

function Is-Protected([string]$Path) {
    $full = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $Root $Path))
    }
    foreach ($item in $Protected) {
        $rooted = [System.IO.Path]::GetFullPath((Join-Path $Root $item))
        if ($full -eq $rooted -or $full.StartsWith($rooted + [System.IO.Path]::DirectorySeparatorChar)) {
            return $true
        }
    }
    return $false
}

function Show-Target([string]$Path) {
    if (Is-Protected $Path) {
        Write-Host "[protected] $Path" -ForegroundColor Yellow
        return
    }
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path
        $size = if ($item.PSIsContainer) {
            [math]::Round(((Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object Length -Sum).Sum / 1MB), 1)
        } else {
            [math]::Round(($item.Length / 1MB), 1)
        }
        Write-Host ("[delete]    {0}  ({1} MB)" -f $Path, $size)
    }
}

function Remove-Target([string]$Path) {
    if (Is-Protected $Path -or -not (Test-Path -LiteralPath $Path)) { return }
    if ($Apply) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-Host "[deleted]   $Path" -ForegroundColor Green
    }
}

function Assert-ServicesStopped {
    if ($Force) { return }
    $running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'wechat_ai_bridge\s+(run|qq|singing-worker|panel)' }
    if ($running) {
        $ids = ($running | ForEach-Object { $_.ProcessId }) -join ', '
        throw "检测到网关/worker/面板仍在运行（PID: $ids）。请先停止，或加 -Force。"
    }
}

Write-Host '=== 保护目录 ===' -ForegroundColor Cyan
$Protected | ForEach-Object { Write-Host "  $_" }

Write-Host ''
Write-Host '=== 缓存 ===' -ForegroundColor Cyan
$CacheTargets | Sort-Object -Unique | ForEach-Object { Show-Target $_ }

Write-Host ''
Write-Host '=== 数据库 ===' -ForegroundColor Cyan
$DatabaseTargets | ForEach-Object {
    Write-Host "  $_"
    foreach ($suffix in @('-wal', '-shm')) {
        if (Test-Path -LiteralPath ($_ + $suffix)) { Write-Host "    $_$suffix" }
    }
}

if (-not $Apply) {
    Write-Host ''
    Write-Host '这是预览。确认后运行：' -ForegroundColor Yellow
    Write-Host '  .\scripts\clean.ps1 -Apply'
    Write-Host '长期记忆数据库默认不清理；需要时加 -IncludeMemory。'
    return
}

Assert-ServicesStopped

$backupDir = $null
if (-not $NoBackup) {
    $backupDir = Join-Path $Root ('data/backups/cleanup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    foreach ($db in $DatabaseTargets) {
        if (Test-Path -LiteralPath $db) {
            Copy-Item -LiteralPath $db -Destination (Join-Path $backupDir ([System.IO.Path]::GetFileName($db))) -Force
        }
    }
    Write-Host "数据库备份：$backupDir" -ForegroundColor Cyan
}

$CacheTargets | Sort-Object -Unique | ForEach-Object { Remove-Target $_ }
foreach ($db in $DatabaseTargets) {
    foreach ($path in @($db, $db + '-wal', $db + '-shm')) {
        Remove-Target $path
    }
}

Get-ChildItem -Path 'src', 'tests', 'scripts' -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Target $_.FullName }
Get-ChildItem -Path 'src', 'tests', 'scripts' -Recurse -File -Include '*.pyc' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Target $_.FullName }

Write-Host ''
Write-Host '清理完成。' -ForegroundColor Green
Write-Host '保留：data/music、data/memes、data/runtime/gpt-sovits、tools 下的音色、配置、凭据、知识库。'
