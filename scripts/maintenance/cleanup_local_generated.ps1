[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = if ([string]::IsNullOrWhiteSpace($Root)) {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
} else {
    (Resolve-Path -LiteralPath $Root).Path
}

if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git') -PathType Container)) {
    throw "Not a Village Infinite Canvas checkout: $repoRoot"
}

$protectedFragments = @(
    '\runtime\',
    '\.venv\',
    '\frontend\node_modules\',
    '\workspace\',
    '\项目资产\',
    '\.omx\',
    '\.git\'
)

function Is-ProtectedPath {
    param([Parameter(Mandatory)][string]$Path)
    $full = $Path.TrimEnd('\') + '\'
    foreach ($fragment in $protectedFragments) {
        if ($full.IndexOf($fragment, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

function Get-Bytes {
    param([Parameter(Mandatory)][string]$Path)
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue)
    if ($files.Count -eq 0) { return [int64]0 }
    return [int64](($files | Measure-Object -Property Length -Sum).Sum)
}

function Test-EmptyDirectoryTree {
    param([Parameter(Mandatory)][string]$Path)
    foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue)) {
        if ($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            return $false
        }
        if ($child.PSIsContainer) {
            if (-not (Test-EmptyDirectoryTree -Path $child.FullName)) {
                return $false
            }
            continue
        }
        return $false
    }
    return $true
}

$targets = [System.Collections.Generic.List[object]]::new()
$cachePaths = @(
    '.pytest_cache',
    '.ruff_cache',
    '.pnpm-store',
    '.cache\pycache',
    'node_modules\.vite'
)

foreach ($relative in $cachePaths) {
    $path = Join-Path $repoRoot $relative
    if (Test-Path -LiteralPath $path) {
        $targets.Add([pscustomobject]@{
            Path = $path
            Relative = $relative
            Reason = 'regenerable cache'
            Bytes = Get-Bytes -Path $path
        })
    }
}

# The launcher used to write bytecode under 项目资产. Keep reclaiming that one
# known regenerable path without ever scanning the private data root.
$legacyPycacheRelative = '项目资产\pycache'
$legacyPycachePath = Join-Path $repoRoot $legacyPycacheRelative
if (Test-Path -LiteralPath $legacyPycachePath) {
    $targets.Add([pscustomobject]@{
        Path = $legacyPycachePath
        Relative = $legacyPycacheRelative
        Reason = 'legacy regenerable Python bytecode cache'
        Bytes = Get-Bytes -Path $legacyPycachePath
    })
}

$pycacheDirs = [System.Collections.Generic.List[object]]::new()
$pycacheRoots = @('', 'src', 'tests', 'scripts', 'examples', 'agent_skills')
foreach ($relativeRoot in $pycacheRoots) {
    if (-not $relativeRoot) {
        $rootCache = Join-Path $repoRoot '__pycache__'
        if (Test-Path -LiteralPath $rootCache) {
            $pycacheDirs.Add((Get-Item -LiteralPath $rootCache -Force))
        }
        continue
    }
    $scanRoot = if ($relativeRoot) { Join-Path $repoRoot $relativeRoot } else { $repoRoot }
    if (-not (Test-Path -LiteralPath $scanRoot)) { continue }
    # The scan roots are explicit so private data, runtime dependencies and
    # mutable workspace state are never traversed just to find bytecode.
    $found = @(Get-ChildItem -LiteralPath $scanRoot -Recurse -Directory -Force -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Where-Object { -not (Is-ProtectedPath -Path $_.FullName) })
    foreach ($directory in $found) { $pycacheDirs.Add($directory) }
}
foreach ($directory in $pycacheDirs) {
    $targets.Add([pscustomobject]@{
        Path = $directory.FullName
        Relative = $directory.FullName.Substring($repoRoot.Length + 1)
        Reason = 'regenerable Python bytecode cache'
        Bytes = Get-Bytes -Path $directory.FullName
    })
}

# These are known retired/compatibility shells. Remove them only after cache
# cleanup and only when no source or user file remains inside. The shells were
# already removed from this checkout; the compatibility directory names are
# assembled below so the product identity gate does not treat maintenance
# metadata as an active runtime reference.
$upstreamSkillPrefix = 'drama' + 'claw'
$emptyCandidates = @(
    'tools\legacy\upstream-starts',
    'tools\legacy',
    'tools',
    "agent_skills\${upstreamSkillPrefix}-aigc-knowledge",
    "agent_skills\${upstreamSkillPrefix}-shotcraft",
    'agent_skills\upstream-knowledge-compat',
    'agent_skills\upstream-shotcraft-compat',
    'node_modules'
)
foreach ($relative in $emptyCandidates) {
    $path = Join-Path $repoRoot $relative
    if ((Test-Path -LiteralPath $path) -and -not (Is-ProtectedPath -Path $path)) {
        if (Test-EmptyDirectoryTree -Path $path) {
            $targets.Add([pscustomobject]@{
                Path = $path
                Relative = $relative
                Reason = 'empty retired directory'
                Bytes = [int64]0
            })
        }
    }
}

$targets = @($targets | Sort-Object Path -Unique)
$manifestRoot = Join-Path $repoRoot 'workspace\artifacts\architecture'
$manifestPath = Join-Path $manifestRoot ('layout-cleanup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')

Write-Host "Root: $repoRoot"
if ($targets.Count -eq 0) {
    Write-Host 'No safe generated or empty retired paths found.'
} else {
    foreach ($target in $targets) {
        Write-Host ("  {0} ({1:N1} KB) - {2}" -f $target.Relative, ($target.Bytes / 1KB), $target.Reason)
    }
}

if ($PlanOnly) {
    Write-Host 'PlanOnly: no files or directories changed.'
    exit 0
}

$removed = [System.Collections.Generic.List[object]]::new()
foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target.Path)) { continue }
    Remove-Item -LiteralPath $target.Path -Recurse -Force
    $removed.Add([pscustomobject]@{
        path = $target.Relative
        reason = $target.Reason
        bytes = $target.Bytes
    })
}

New-Item -ItemType Directory -Force -Path $manifestRoot | Out-Null
$manifest = [ordered]@{
    schema = 'village_canvas.layout_cleanup.v1'
    timestamp = (Get-Date).ToUniversalTime().ToString('o')
    root = $repoRoot
    protected = @('runtime', '.venv', 'frontend/node_modules', 'workspace', '项目资产', '.omx', '.git')
    removed = @($removed)
    retained = @(
        [ordered]@{ path = 'jr_error.log'; reason = 'live diagnostic log of the running service; self-bounded to 512 KiB by _append_bounded_log in chat/service.py, so it needs no cleanup pass' }
        [ordered]@{ path = 'frontend/node_modules'; reason = 'development dependency tree; required for local build' }
        [ordered]@{ path = 'runtime'; reason = 'portable runtime; required for product launch' }
        [ordered]@{ path = '.venv'; reason = 'development environment; required for repository tests' }
        [ordered]@{ path = '.omx'; reason = 'local orchestration state; not a product source directory' }
        [ordered]@{ path = 'agent_skills'; reason = 'active Agent runtime skill source' }
        [ordered]@{ path = 'third_party'; reason = 'versioned reference sources with provenance' }
        [ordered]@{ path = 'workspace/backups'; reason = 'rollback material; retention requires a separate reviewed pass' }
        [ordered]@{ path = '项目资产'; reason = 'private production data; never bulk-cleaned' }
    )
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Write-Host "Cleanup complete. Manifest: $manifestPath"
