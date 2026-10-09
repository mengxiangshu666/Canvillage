[CmdletBinding()]
param(
    [string]$TargetRoot = '',
    [switch]$SkipBuild,
    [switch]$PlanOnly,
    [switch]$ForceRestart
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$TargetRoot = if ([string]::IsNullOrWhiteSpace($TargetRoot)) { $repoRoot } else { $TargetRoot }
$targetRootResolved = (Resolve-Path -LiteralPath $TargetRoot).Path
$sourceBackend = Join-Path $repoRoot 'src\novelvideo'
$targetBackend = Join-Path $targetRootResolved 'runtime\env\novelvideo'
$sourceFrontend = Join-Path $repoRoot 'frontend\dist'
$targetFrontend = Join-Path $targetRootResolved 'frontend\dist'
$sourceAgentSkills = Join-Path $repoRoot 'agent_skills'
$targetAgentSkills = Join-Path $targetRootResolved 'agent_skills'
$stopScript = Join-Path $targetRootResolved '_stop.ps1'
$launchScript = Join-Path $targetRootResolved 'village_canvas_launch.py'
$pythonExe = Join-Path $targetRootResolved 'runtime\python\python.exe'
$apiUrl = 'http://127.0.0.1:8784/healthz'
$retiredProductId = 'dramaclaw'  # identity-allow: retirement migration, read-old/write-new (src\novelvideo\chat\identity_compat.py)
$projectStateRoot = Join-Path $targetRootResolved '项目资产\state'
$projectRuntimeRoot = Join-Path $targetRootResolved '项目资产\runtime'

foreach ($path in @($sourceBackend, $targetBackend, $sourceFrontend, $targetFrontend, $sourceAgentSkills, $stopScript, $launchScript, $pythonExe)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required Village Infinite Canvas deployment path is missing: $path"
    }
}

function Test-ExcludedDeploymentFile {
    param([Parameter(Mandatory)][IO.FileInfo]$File)

    $parts = $File.FullName -split '[\\/]'
    if ($parts -contains '__pycache__' -or $parts -contains '.pytest_cache' -or $parts -contains '.ruff_cache') {
        return $true
    }
    return $File.Extension -in @('.pyc', '.pyo', '.log', '.db', '.sqlite', '.sqlite3')
}

function Get-RelativeDeploymentPath {
    param(
        [Parameter(Mandatory)][string]$Base,
        [Parameter(Mandatory)][string]$Path
    )

    $baseFull = [IO.Path]::GetFullPath($Base).TrimEnd('\', '/')
    $pathFull = [IO.Path]::GetFullPath($Path)
    if (
        $pathFull.Length -le $baseFull.Length -or
        -not $pathFull.StartsWith($baseFull, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Path is outside deployment base: $Path"
    }
    return $pathFull.Substring($baseFull.Length).TrimStart('\', '/')
}

function Get-TreeChanges {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination,
        [Parameter(Mandatory)][string]$Area
    )

    $changes = [Collections.Generic.List[object]]::new()
    foreach ($sourceFile in Get-ChildItem -LiteralPath $Source -Recurse -File -Force) {
        if (Test-ExcludedDeploymentFile $sourceFile) { continue }
        $relative = Get-RelativeDeploymentPath -Base $Source -Path $sourceFile.FullName
        $destinationFile = Join-Path $Destination $relative
        $exists = Test-Path -LiteralPath $destinationFile -PathType Leaf
        $changed = -not $exists
        if ($exists) {
            $changed = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash -ne
                (Get-FileHash -LiteralPath $destinationFile -Algorithm SHA256).Hash
        }
        if ($changed) {
            $changes.Add([pscustomobject]@{
                Area = $Area
                Relative = $relative
                Source = $sourceFile.FullName
                Destination = $destinationFile
                Exists = $exists
            })
        }
    }
    return @($changes)
}

function Get-StaleDeploymentFiles {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination,
        [Parameter(Mandatory)][string]$Area
    )

    # `Get-TreeChanges` only walks the source, so a file that was renamed or
    # deleted in the repo survives in the runtime tree forever -- and gets
    # shipped. Walking the destination closes that hole.
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) { return @() }
    $stale = [Collections.Generic.List[object]]::new()
    foreach ($destinationFile in Get-ChildItem -LiteralPath $Destination -Recurse -File -Force) {
        if (Test-ExcludedDeploymentFile $destinationFile) { continue }
        $relative = Get-RelativeDeploymentPath -Base $Destination -Path $destinationFile.FullName
        if (Test-Path -LiteralPath (Join-Path $Source $relative) -PathType Leaf) { continue }
        $stale.Add([pscustomobject]@{
            Area = $Area
            Relative = $relative
            Source = $null
            Destination = $destinationFile.FullName
            Exists = $true
            Stale = $true
        })
    }
    return @($stale)
}

function Get-NewestWriteTimeUtc {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $null }
    $newest = $null
    foreach ($file in Get-ChildItem -LiteralPath $Path -Recurse -File -Force) {
        if (Test-ExcludedDeploymentFile $file) { continue }
        if ($null -eq $newest -or $file.LastWriteTimeUtc -gt $newest) {
            $newest = $file.LastWriteTimeUtc
        }
    }
    return $newest
}

function Assert-FrontendBuildIsCurrent {
    # 前端是**构建产物**：改完源码不重新构建，服务端照旧发旧包，而 API 健康、
    # 页面能开，从外部完全看不出陈旧。2026-09-30 用户追问才发现部署长期带
    # -SkipBuild（跳过前端构建），而脚本里没有任何检查会提示产物已过期。
    # 这道检查**不看 -SkipBuild，每次都跑**，所以它不能被静默绕过。
    param(
        [Parameter(Mandatory)][string]$SourceRoot,
        [Parameter(Mandatory)][string]$DistRoot
    )

    $indexHtml = Join-Path $DistRoot 'index.html'
    if (-not (Test-Path -LiteralPath $indexHtml -PathType Leaf)) {
        throw "frontend build missing: $indexHtml. Run the deploy without -SkipBuild."
    }
    $sourceNewest = Get-NewestWriteTimeUtc -Path $SourceRoot
    $distNewest = Get-NewestWriteTimeUtc -Path $DistRoot
    if ($null -eq $sourceNewest -or $null -eq $distNewest) { return }
    if ($sourceNewest -gt $distNewest) {
        $stamp = 'frontend build is stale: newest source {0:o} is newer than newest dist {1:o}.' -f $sourceNewest, $distNewest
        throw ($stamp + ' The API would look healthy while serving an old SPA. Run the deploy without -SkipBuild to rebuild.')
    }
}

function Test-ApiHealthy {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $apiUrl
        return $response.StatusCode -eq 200 -and $response.Content -match '"status"\s*:\s*"ok"'
    }
    catch {
        return $false
    }
}

# Fail before building or touching the live installation, including -SkipBuild.
$devPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $devPython -PathType Leaf)) {
    throw 'Source validation requires the project .venv Python.'
}
$sizeCheck = Join-Path $repoRoot 'scripts\architecture\check_file_sizes.py'
$sizeReport = & $devPython $sizeCheck --root $repoRoot --fail-on high --format text
if ($LASTEXITCODE -ne 0) {
    $sizeReport | Out-Host
    throw 'Source file size gate failed; deployment stopped before any changes.'
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupRoot = Join-Path $targetRootResolved "_deploy_backups\village-canvas-$timestamp"
$prebuildNewFiles = @()
$builtFrontendChanges = @()
$sameFrontendRoot = [IO.Path]::GetFullPath($sourceFrontend).TrimEnd('\', '/') -eq [IO.Path]::GetFullPath($targetFrontend).TrimEnd('\', '/')

# A local build replaces the live dist in place. Preserve its complete previous
# contents before invoking the builder, including when the build later fails.
if (-not $SkipBuild -and -not $PlanOnly -and $sameFrontendRoot) {
    $savedFrontend = Join-Path $backupRoot 'frontend'
    New-Item -ItemType Directory -Force -Path $savedFrontend | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $targetFrontend -Recurse -File -Force) {
        if (Test-ExcludedDeploymentFile -File $file) { continue }
        $relative = Get-RelativeDeploymentPath -Base $targetFrontend -Path $file.FullName
        $savedFile = Join-Path $savedFrontend $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $savedFile) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $savedFile -Force
    }
    [ordered]@{ schema = 'village_deployment_rollback.v1'; newFiles = @() } |
        ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $backupRoot 'rollback-manifest.json') -Encoding UTF8
}

if (-not $SkipBuild -and -not $PlanOnly) {
    Push-Location (Join-Path $repoRoot 'frontend')
    try {
        pnpm run build:supercanvas
        if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }
    }
    finally {
        Pop-Location
        if ($sameFrontendRoot) {
            $builtFrontendChanges = @(Get-TreeChanges -Source $sourceFrontend -Destination $savedFrontend -Area 'frontend')
            $prebuildNewFiles = @($builtFrontendChanges | Where-Object { -not $_.Exists } | ForEach-Object {
                [ordered]@{ area = $_.Area; relative = $_.Relative }
            })
            [ordered]@{ schema = 'village_deployment_rollback.v1'; newFiles = $prebuildNewFiles } |
                ConvertTo-Json -Depth 5 |
                Set-Content -LiteralPath (Join-Path $backupRoot 'rollback-manifest.json') -Encoding UTF8
        }
    }
}

# 放在构建之后：带构建时这里验的是刚产出的包，必然新鲜；带 -SkipBuild 时
# 这道检查才会拦下「源码已改、产物未重建」的部署。
if (-not $PlanOnly) {
    Assert-FrontendBuildIsCurrent -SourceRoot (Join-Path $repoRoot 'frontend\src') -DistRoot $sourceFrontend
}

$changes = @(
    Get-TreeChanges -Source $sourceBackend -Destination $targetBackend -Area 'backend'
    Get-TreeChanges -Source $sourceFrontend -Destination $targetFrontend -Area 'frontend'
    Get-TreeChanges -Source $sourceAgentSkills -Destination $targetAgentSkills -Area 'agent-skills'
)
$staleFiles = @(
    Get-StaleDeploymentFiles -Source $sourceBackend -Destination $targetBackend -Area 'backend'
    Get-StaleDeploymentFiles -Source $sourceFrontend -Destination $targetFrontend -Area 'frontend'
    Get-StaleDeploymentFiles -Source $sourceAgentSkills -Destination $targetAgentSkills -Area 'agent-skills'
)
if (-not $PlanOnly -and $changes.Count -eq 0 -and $staleFiles.Count -eq 0 -and $builtFrontendChanges.Count -eq 0 -and (Test-ApiHealthy) -and -not $ForceRestart) {
    [pscustomobject]@{
        deployed = $false
        reason = 'already synchronized'
        target = $targetRootResolved
        backup = if (Test-Path -LiteralPath $backupRoot) { $backupRoot } else { $null }
        healthy = Test-ApiHealthy
    }
    return
}

if ($PlanOnly) {
    [pscustomobject]@{
        planned = $true
        buildRequested = -not $SkipBuild
        buildExecuted = $false
        target = $targetRootResolved
        backendFiles = @($changes | Where-Object Area -eq 'backend').Count
        frontendFiles = @($changes | Where-Object Area -eq 'frontend').Count
        agentSkillFiles = @($changes | Where-Object Area -eq 'agent-skills').Count
        staleFiles = $staleFiles.Count
        stalePaths = @($staleFiles | ForEach-Object { "$($_.Area)/$($_.Relative)" })
        healthy = Test-ApiHealthy
    }
    return
}

$wasHealthy = Test-ApiHealthy

# Persist the inverse of newly introduced files before changing the runtime.
# Backups of overwritten/deleted files alone cannot undo additions.
if ($changes.Count -gt 0 -or $staleFiles.Count -gt 0 -or (Test-Path -LiteralPath $backupRoot)) {
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    $rollbackManifest = [ordered]@{
        schema = 'village_deployment_rollback.v1'
        newFiles = @($prebuildNewFiles) + @($changes | Where-Object { -not $_.Exists } | ForEach-Object {
            [ordered]@{ area = $_.Area; relative = $_.Relative }
        })
    }
    $rollbackManifest | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath (Join-Path $backupRoot 'rollback-manifest.json') -Encoding UTF8
}

if ($wasHealthy) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript
    for ($attempt = 0; $attempt -lt 30 -and (Test-ApiHealthy); $attempt++) {
        Start-Sleep -Milliseconds 500
    }
    if (Test-ApiHealthy) {
        throw 'Village Infinite Canvas did not stop cleanly on port 8784.'
    }
}

foreach ($change in $changes) {
    $destinationDirectory = Split-Path -Parent $change.Destination
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    if ($change.Exists) {
        $backupFile = Join-Path $backupRoot (Join-Path $change.Area $change.Relative)
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupFile) | Out-Null
        Copy-Item -LiteralPath $change.Destination -Destination $backupFile -Force
    }
    Copy-Item -LiteralPath $change.Source -Destination $change.Destination -Force
}

# Stale mirrors are the rename/deletion hole in `Get-TreeChanges`: back them up,
# then delete so the runtime tree stops shipping files the repo no longer has.
$removedStaleFiles = 0
foreach ($stale in $staleFiles) {
    $backupFile = Join-Path $backupRoot (Join-Path $stale.Area $stale.Relative)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backupFile) | Out-Null
    Copy-Item -LiteralPath $stale.Destination -Destination $backupFile -Force
    Remove-Item -LiteralPath $stale.Destination -Force
    $removedStaleFiles++
}

foreach ($change in $changes) {
    $sourceHash = (Get-FileHash -LiteralPath $change.Source -Algorithm SHA256).Hash
    $targetHash = (Get-FileHash -LiteralPath $change.Destination -Algorithm SHA256).Hash
    if ($sourceHash -ne $targetHash) {
        throw "Post-copy hash verification failed: $($change.Area)/$($change.Relative)"
    }
}

Start-Process -FilePath $pythonExe -ArgumentList @($launchScript) -WorkingDirectory $targetRootResolved -WindowStyle Hidden
for ($attempt = 0; $attempt -lt 45 -and -not (Test-ApiHealthy); $attempt++) {
    Start-Sleep -Seconds 1
}
if (-not (Test-ApiHealthy)) {
    throw "Village Infinite Canvas failed to become healthy after deployment. Backup: $backupRoot"
}

if ($env:OS -eq 'Windows_NT') {
    $retentionScript = Join-Path $repoRoot 'scripts\maintenance\recycle_deployment_backups.ps1'
    if (Test-Path -LiteralPath $retentionScript) {
        try { & $retentionScript -Root $targetRootResolved | Out-Null }
        catch { Write-Warning "Deployment succeeded, but backup recycling failed: $($_.Exception.Message)" }
    }
}

[pscustomobject]@{
    deployed = $true
    target = $targetRootResolved
    backendFiles = @($changes | Where-Object Area -eq 'backend').Count
    frontendFiles = @($changes | Where-Object Area -eq 'frontend').Count
    frontendBuiltFiles = $builtFrontendChanges.Count
    agentSkillFiles = @($changes | Where-Object Area -eq 'agent-skills').Count
    removedStaleFiles = $removedStaleFiles
    backup = $backupRoot
    healthy = $true
}
