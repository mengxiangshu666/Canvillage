[CmdletBinding()]
param(
    [string]$OutputRoot = '',
    [string]$Version = '',
    [switch]$IncludeProjectAssets,
    [switch]$PlanOnly,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$OutputRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    Join-Path $repoRoot 'artifacts\releases'
} else {
    [IO.Path]::GetFullPath($OutputRoot)
}

function Get-ProjectVersion {
    $pyproject = Join-Path $repoRoot 'pyproject.toml'
    $match = Select-String -LiteralPath $pyproject -Pattern '^version\s*=\s*["'']([^"'']+)["'']' | Select-Object -First 1
    if (-not $match) {
        throw "Could not read project version from $pyproject"
    }
    return $match.Matches[0].Groups[1].Value
}

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = Get-ProjectVersion
}
$safeVersion = $Version -replace '[^A-Za-z0-9._-]', '-'

$gitHead = ''
$gitStatus = @()
try {
    $gitHead = (& git -C $repoRoot rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
    $gitStatus = @(& git -C $repoRoot status --short 2>$null)
} catch {
    $gitHead = ''
    $gitStatus = @()
}
$isDirty = $gitStatus.Count -gt 0
$snapshotLabel = if ($isDirty) { 'dirty' } else { 'clean' }
$packageName = "village-canvas-v$safeVersion-windows-x64-$snapshotLabel"
$packageRoot = Join-Path $OutputRoot $packageName

function Test-PathWithin {
    param(
        [Parameter(Mandatory)][string]$Candidate,
        [Parameter(Mandatory)][string]$Base
    )
    $candidateFull = [IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
    $baseFull = [IO.Path]::GetFullPath($Base).TrimEnd('\', '/')
    return $candidateFull.StartsWith($baseFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        $candidateFull.Equals($baseFull, [StringComparison]::OrdinalIgnoreCase)
}

function Remove-GeneratedReleaseDirectory {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$OutputRoot
    )

    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    $resolvedOutputRoot = (Resolve-Path -LiteralPath $OutputRoot).Path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    if (-not (Test-PathWithin -Candidate $resolvedPath -Base $resolvedOutputRoot)) {
        throw "Refusing to remove a release outside OutputRoot: $resolvedPath"
    }

    $item = Get-Item -LiteralPath $resolvedPath -Force
    if (-not $item.PSIsContainer) {
        throw "Release output is not a directory: $resolvedPath"
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a reparse-point release directory: $resolvedPath"
    }

    # The target is an ignored, builder-owned directory. Directory.Delete is
    # more reliable than Remove-Item for a large Windows tree after a cancelled
    # build, while the containment and reparse-point checks stay explicit.
    $lastError = $null
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            [IO.Directory]::Delete($resolvedPath, $true)
        }
        catch {
            $lastError = $_
            Start-Sleep -Seconds 2
        }
        if (-not (Test-Path -LiteralPath $resolvedPath)) {
            return
        }
    }
    throw "Could not remove generated release directory after 5 attempts: $resolvedPath. $($lastError.Exception.Message)"
}

if (-not (Test-PathWithin -Candidate $packageRoot -Base $OutputRoot)) {
    throw "Release output escaped OutputRoot: $packageRoot"
}

if ((Test-Path -LiteralPath $packageRoot) -and -not $PlanOnly) {
    if (-not $Force) {
        throw "Release directory already exists: $packageRoot (use -Force only for this generated release directory)"
    }
    Remove-GeneratedReleaseDirectory -Path $packageRoot -OutputRoot $OutputRoot
}

$singleFiles = @(
    'village_canvas_launch.py',
    'village_canvas_run_api.py',
    'village_canvas_open_when_ready.py',
    'village_canvas_rotate_log.py',
    'village_canvas_cli.py',
    'village_canvas_cli_hidden.ps1',
    '_stop.ps1',
    '启动村长无限画布.bat',
    '启动村长无限画布.vbs',
    '村长无限画布-Start.bat',
    'village-canvas.bat',
    'README.md',
    'README-Windows.txt',
    'PROJECT_LAYOUT.md',
    'LICENSES',
    'NOTICE',
    'REUSE.toml'
)

$treeRoots = @(
    'src',
    'frontend\dist',
    'runtime',
    'agent_skills'
)

$excludedDirectoryNames = @(
    '.git', '.omx', '.venv', 'node_modules', '.pnpm-store', '.cache',
    '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache',
    'test-results', 'dist-ssr', 'output', '.quarkclouddrive'
)
$excludedExtensions = @('.pyc', '.pyo', '.log', '.db', '.sqlite', '.sqlite3', '.tmp', '.bak')

function Test-ExcludedRelativePath {
    param(
        [Parameter(Mandatory)][string]$RelativePath,
        [Parameter(Mandatory)][string]$SourceRoot
    )
    $parts = @($RelativePath -split '[\\/]')
    foreach ($part in $parts) {
        if ($excludedDirectoryNames -contains $part) { return $true }
    }
    # Legacy Hermes workspaces and runtime bundles are not part of the native
    # Village Agent. Keep them out of every portable release.
    if (
        $SourceRoot -match '[\\/]runtime$' -and
        $parts.Count -gt 0 -and
        $parts[0] -in @('.hermes', 'hermes')
    ) {
        return $true
    }
    # Quark Drive keeps credentials beside the skill by default. The live
    # directory is a junction into 项目资产\state and must never be packaged.
    if ($parts.Count -ge 2 -and $parts[0] -eq 'quarkclouddrive' -and $parts[1] -eq 'codex') {
        return $true
    }
    $fileName = [IO.Path]::GetFileName($RelativePath)
    $extension = [IO.Path]::GetExtension($fileName).ToLowerInvariant()
    if ($excludedExtensions -contains $extension) { return $true }
    # Root environment files and runtime state never belong in a release.
    if ($SourceRoot -eq $repoRoot -and ($fileName -eq '.env' -or $fileName.StartsWith('.env.'))) {
        return $true
    }
    return $false
}

$copyPlan = [Collections.Generic.List[object]]::new()

function Add-FileToPlan {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$DestinationRelative
    )
    $sourceResolved = (Resolve-Path -LiteralPath $Source).Path
    $sourceRoot = $repoRoot
    if (Test-ExcludedRelativePath -RelativePath ([IO.Path]::GetFileName($sourceResolved)) -SourceRoot $sourceRoot) {
        return
    }
    $copyPlan.Add([pscustomobject]@{
        Source = $sourceResolved
        Destination = $DestinationRelative.Replace('/', '\')
    })
}

function Add-TreeToPlan {
    param(
        [Parameter(Mandatory)][string]$RelativeRoot,
        [Parameter(Mandatory)][string]$DestinationRoot
    )
    $source = Join-Path $repoRoot $RelativeRoot
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Required release tree is missing: $source"
    }
    $sourceFull = [IO.Path]::GetFullPath($source).TrimEnd('\', '/')
    foreach ($file in Get-ChildItem -LiteralPath $source -Recurse -File -Force) {
        $relative = $file.FullName.Substring($sourceFull.Length).TrimStart([char[]]'\\/')
        if (Test-ExcludedRelativePath -RelativePath $relative -SourceRoot $sourceFull) { continue }
        $destination = Join-Path $DestinationRoot $relative
        $copyPlan.Add([pscustomobject]@{
            Source = $file.FullName
            Destination = $destination.Replace('/', '\')
        })
    }
}

foreach ($file in $singleFiles) {
    $source = Join-Path $repoRoot $file
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        Add-FileToPlan -Source $source -DestinationRelative $file
    } elseif (Test-Path -LiteralPath $source -PathType Container) {
        Add-TreeToPlan -RelativeRoot $file -DestinationRoot $file
    } else {
        throw "Required release item is missing: $source"
    }
}
foreach ($tree in $treeRoots) {
    Add-TreeToPlan -RelativeRoot $tree -DestinationRoot $tree
}

if ($IncludeProjectAssets) {
    Write-Warning 'IncludeProjectAssets copies user data into the release. Verify the source manually before sharing the package.'
    Add-TreeToPlan -RelativeRoot '项目资产' -DestinationRoot '项目资产'
}

$copyPlan = @($copyPlan | Sort-Object Destination -Unique)
$payloadBytes = ($copyPlan | ForEach-Object { (Get-Item -LiteralPath $_.Source).Length } | Measure-Object -Sum).Sum
Write-Host ("Package: {0}" -f $packageName)
Write-Host ("Files: {0}; payload: {1:N1} GB" -f $copyPlan.Count, ($payloadBytes / 1GB))
Write-Host ("Source: {0} ({1})" -f ($(if ($gitHead) { $gitHead.Substring(0, [Math]::Min(12, $gitHead.Length)) } else { 'unknown' }), $snapshotLabel))

if ($PlanOnly) {
    Write-Host 'PlanOnly: no files were copied.'
    $copyPlan |
        Group-Object { ($_.Destination -split '[\\/]')[0] } |
        Sort-Object Name |
        ForEach-Object { Write-Host ("  {0}: {1} files" -f $_.Name, $_.Count) }
    exit 0
}

New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null
foreach ($item in $copyPlan) {
    $destination = Join-Path $packageRoot $item.Destination
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Copy-Item -LiteralPath $item.Source -Destination $destination -Force
}

# The launcher creates these directories on first boot; keeping the data root
# visible makes the portable package layout understandable before first run.
foreach ($directory in @('项目资产', '项目资产\项目', '项目资产\state', '项目资产\output', '项目资产\runtime', '项目资产\logs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot $directory) | Out-Null
}

$frontendBuild = $null
$frontendVersion = Join-Path $packageRoot 'frontend\dist\version.json'
if (Test-Path -LiteralPath $frontendVersion -PathType Leaf) {
    try { $frontendBuild = Get-Content -LiteralPath $frontendVersion -Raw | ConvertFrom-Json } catch { $frontendBuild = $null }
}

$buildInfo = [ordered]@{
    product = 'village-infinite-canvas'
    displayName = '村长无限画布'
    packageVersion = $Version
    packageName = $packageName
    architecture = 'windows-x64'
    status = $(if ($isDirty) { 'development-snapshot' } else { 'source-snapshot' })
    generatedAtUtc = [DateTime]::UtcNow.ToString('o')
    sourceCommit = if ($gitHead) { $gitHead } else { $null }
    sourceDirty = $isDirty
    frontendBuildId = if ($frontendBuild) { [string]$frontendBuild.buildId } else { $null }
    runtimeContract = '127.0.0.1:8784'
    privateProjectAssetsIncluded = [bool]$IncludeProjectAssets
}
$buildInfo | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $packageRoot 'BUILD_INFO.json') -Encoding UTF8

$readme = @'
村长无限画布 Windows 便携版
============================

这是由白名单生成的独立运行包，不是开发源码目录。

启动
----
双击“启动村长无限画布.vbs”，或双击“启动村长无限画布.bat”。
服务默认监听 http://127.0.0.1:8784，并在服务就绪后打开浏览器。
停止服务：运行同目录下的 _stop.ps1。

数据
----
项目、画布、素材、生成结果、模型配置和 Agent 成长记忆放在同目录的“项目资产”中。
本发行包默认不携带原开发目录的私人数据。迁移旧数据时，在停止服务后复制旧目录的“项目资产”到本目录，
不要复制 .venv、node_modules、.pnpm-store、_task_backups、_deploy_backups 或整个开发目录。

校验
----
MANIFEST.json 记录发行包文件和 SHA256；SHA256SUMS 可用 PowerShell 的 Get-FileHash 复核。
BUILD_INFO.json 记录源码提交、前端 Build ID 和是否为 dirty 工作区快照。

说明
----
当前包保留项目的 Python/FFmpeg/Node 运行能力，但不把开发缓存和旧运行工作区带入。
'@
$readme | Set-Content -LiteralPath (Join-Path $packageRoot 'README-Portable.txt') -Encoding UTF8

$manifestPath = Join-Path $packageRoot 'MANIFEST.json'
$checksumsPath = Join-Path $packageRoot 'SHA256SUMS'
$payloadFiles = @(Get-ChildItem -LiteralPath $packageRoot -Recurse -File -Force | Where-Object {
    $_.FullName -ne $manifestPath -and $_.FullName -ne $checksumsPath
})
$manifestFiles = foreach ($file in $payloadFiles) {
    $relative = $file.FullName.Substring($packageRoot.Length).TrimStart([char[]]'\\/')
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    [pscustomobject][ordered]@{
        path = $relative.Replace('\', '/')
        bytes = [int64]$file.Length
        sha256 = $hash
    }
}
$manifest = [ordered]@{
    schemaVersion = 1
    product = 'village-infinite-canvas'
    package = $packageName
    generatedAtUtc = [DateTime]::UtcNow.ToString('o')
    payloadFileCount = @($manifestFiles).Count
    payloadBytes = [int64](($manifestFiles | Measure-Object -Property bytes -Sum).Sum)
    exclusions = @('项目资产 by default', '.venv', 'node_modules', '.pnpm-store', 'task/deploy backups', 'legacy Hermes runtime/workspace', 'logs/databases/pycache')
    files = @($manifestFiles)
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

$checksumLines = foreach ($entry in $manifestFiles) {
    "{0} *{1}" -f $entry.sha256, $entry.path
}
$checksumLines | Set-Content -LiteralPath $checksumsPath -Encoding ASCII

Write-Host ("Release package created: {0}" -f $packageRoot) -ForegroundColor Green
Write-Host ("Payload: {0} files, {1:N1} GB" -f @($manifestFiles).Count, ([int64]$manifest.payloadBytes / 1GB))
Write-Host 'Status: development snapshot' -ForegroundColor Yellow
