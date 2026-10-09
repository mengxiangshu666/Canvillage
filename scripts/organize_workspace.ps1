[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = if ([string]::IsNullOrWhiteSpace($Root)) {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
} else {
    (Resolve-Path -LiteralPath $Root).Path
}

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git') -PathType Container)) {
    throw "Not a Village Infinite Canvas checkout: $repoRoot"
}

$workspaceRoot = Join-Path $repoRoot 'workspace'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

# These directories are local, generated, or recoverable.  They are moved as
# same-volume renames, then exposed through hidden junctions so old tooling and
# user scripts keep working without another path migration.
$moves = [ordered]@{
    'artifacts' = 'workspace\artifacts'
    '_task_backups' = 'workspace\backups\tasks'
    '_deploy_backups' = 'workspace\backups\deploy'
    '_integration_backups' = 'workspace\backups\integration'
    'output' = 'workspace\output'
    'state' = 'workspace\state'
}

function Get-TreeSummary {
    param([Parameter(Mandatory)][string]$Path)
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue)
    $measure = if ($files.Count -gt 0) { $files | Measure-Object -Property Length -Sum } else { $null }
    $bytes = if ($measure -and $measure.PSObject.Properties['Sum']) { $measure.Sum } else { 0 }
    [pscustomobject]@{
        path = $Path
        files = $files.Count
        bytes = [int64]$bytes
    }
}

function Test-PortInUse {
    try {
        return $null -ne (Get-NetTCPConnection -LocalPort 8784 -State Listen -ErrorAction Stop | Select-Object -First 1)
    } catch {
        return $false
    }
}

if (-not $PlanOnly -and (Test-PortInUse)) {
    throw '8784 is still listening. Stop 村长无限画布 before reorganizing local state.'
}

$plan = foreach ($entry in $moves.GetEnumerator()) {
    $source = Join-Path $repoRoot $entry.Key
    $destination = Join-Path $repoRoot $entry.Value
    $sourceItem = Get-Item -LiteralPath $source -Force -ErrorAction SilentlyContinue
    $destinationItem = Get-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue

    if ($sourceItem -and (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        [pscustomobject]@{ source = $source; destination = $destination; action = 'already-linked'; summary = $null }
        continue
    }
    if (-not $sourceItem) {
        [pscustomobject]@{ source = $source; destination = $destination; action = 'missing'; summary = $null }
        continue
    }
    if ($destinationItem) {
        throw "Destination already exists; refusing to merge implicitly: $destination"
    }
    [pscustomobject]@{
        source = $source
        destination = $destination
        action = 'move-and-link'
        summary = Get-TreeSummary -Path $source
    }
}

Write-Host "Root: $repoRoot"
Write-Host "Workspace: $workspaceRoot"
foreach ($item in $plan) {
    if ($item.action -eq 'move-and-link') {
        Write-Host ("  MOVE {0} -> {1} ({2} files, {3:N1} GB)" -f $item.source, $item.destination, $item.summary.files, ($item.summary.bytes / 1GB))
    } else {
        Write-Host ("  {0}: {1}" -f $item.action.ToUpperInvariant(), $item.source)
    }
}

if ($PlanOnly) {
    Write-Host 'PlanOnly: no files or links changed.'
    exit 0
}

$migrationRoot = Join-Path $workspaceRoot (Join-Path 'backups\layout-migrations' $stamp)
New-Item -ItemType Directory -Force -Path $migrationRoot | Out-Null
$before = @($plan | ForEach-Object {
    if ($_.summary) {
        [pscustomobject]@{ source = $_.source; destination = $_.destination; action = $_.action; files = $_.summary.files; bytes = $_.summary.bytes }
    } else {
        [pscustomobject]@{ source = $_.source; destination = $_.destination; action = $_.action; files = $null; bytes = $null }
    }
})
$before | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $migrationRoot 'before.json') -Encoding UTF8

foreach ($item in $plan | Where-Object action -eq 'move-and-link') {
    $destinationParent = Split-Path -Parent $item.destination
    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    Move-Item -LiteralPath $item.source -Destination $item.destination
    New-Item -ItemType Junction -Path $item.source -Target $item.destination | Out-Null
    attrib +h +s "$($item.source)" | Out-Null
}

$after = @($plan | ForEach-Object {
    $target = Get-Item -LiteralPath $_.destination -Force -ErrorAction SilentlyContinue
    $link = Get-Item -LiteralPath $_.source -Force -ErrorAction SilentlyContinue
    [pscustomobject]@{
        source = $_.source
        destination = $_.destination
        action = $_.action
        source_is_link = [bool]($link -and (($link.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0))
        destination_exists = [bool]$target
        summary = if ($target -and $target.PSIsContainer) { Get-TreeSummary -Path $target.FullName } else { $null }
    }
})
$after | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath (Join-Path $migrationRoot 'after.json') -Encoding UTF8

$layoutNote = @"
村长无限画布单根目录布局
迁移时间: $(Get-Date -Format o)

程序代码、正式运行环境和启动入口仍在仓库根目录。
本机生成物统一位于 workspace\\：
- workspace\\artifacts       构建包、验收证据、性能报告
- workspace\\backups\\*      任务、部署、集成备份
- workspace\\output          非正式测试输出
- workspace\\state           开发/测试状态；根目录 state 为兼容 junction

根目录同名项是隐藏兼容 junction，旧脚本可继续使用原路径。
本次迁移的前后清单见本目录的 before.json 与 after.json。
"@
$layoutNote | Set-Content -LiteralPath (Join-Path $workspaceRoot 'README.md') -Encoding UTF8

Write-Host "Workspace organization complete. Migration evidence: $migrationRoot"
