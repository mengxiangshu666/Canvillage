[CmdletBinding()]
param(
    [string]$TargetRoot = '',
    [string]$BackupRoot = '',
    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$TargetRoot = if ([string]::IsNullOrWhiteSpace($TargetRoot)) { $repoRoot } else { $TargetRoot }
$target = (Resolve-Path -LiteralPath $TargetRoot).Path
if ([string]::IsNullOrWhiteSpace($BackupRoot)) {
    $latestBackup = Get-ChildItem -LiteralPath (Join-Path $target '_deploy_backups') -Directory -Filter 'village-canvas-*' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latestBackup) {
        throw 'No Village Infinite Canvas deployment backup exists inside the project.'
    }
    $BackupRoot = $latestBackup.FullName
}
$backup = (Resolve-Path -LiteralPath $BackupRoot).Path
$stopScript = Join-Path $target '_stop.ps1'
$launchScript = Join-Path $target 'village_canvas_launch.py'
$pythonExe = Join-Path $target 'runtime\python\python.exe'
$healthUrl = 'http://127.0.0.1:8784/healthz'

foreach ($path in @($stopScript, $launchScript, $pythonExe, $backup)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Rollback prerequisite is missing: $path"
    }
}

# Resolve and validate the complete deletion list before stopping the service
# or restoring any files. Only explicit deployed product areas are allowed.
$manifestNewFiles = @()
$rollbackManifestPath = Join-Path $backup 'rollback-manifest.json'
if (Test-Path -LiteralPath $rollbackManifestPath -PathType Leaf) {
    $manifest = Get-Content -LiteralPath $rollbackManifestPath -Raw | ConvertFrom-Json
    if ($manifest.schema -ne 'village_deployment_rollback.v1') {
        throw 'Unsupported deployment rollback manifest schema.'
    }
    foreach ($entry in $manifest.newFiles) {
        $areaRoot = switch ($entry.area) {
            'backend' { Join-Path $target 'runtime\env\novelvideo' }
            'frontend' { Join-Path $target 'frontend\dist' }
            'agent-skills' { Join-Path $target 'agent_skills' }
            default { throw "Unsupported rollback area: $($entry.area)" }
        }
        $relative = [string]$entry.relative
        if ([string]::IsNullOrWhiteSpace($relative) -or [IO.Path]::IsPathRooted($relative)) {
            throw 'Rollback manifest paths must be non-empty relative file paths.'
        }
        $areaFull = [IO.Path]::GetFullPath($areaRoot).TrimEnd('\', '/')
        $candidate = [IO.Path]::GetFullPath((Join-Path $areaFull $relative))
        if (-not $candidate.StartsWith($areaFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Rollback deletion escaped its product area: $relative"
        }
        $manifestNewFiles += $candidate
    }
}

if ($PlanOnly) {
    [pscustomobject]@{
        planned = $true
        target = $target
        backup = $backup
        newFilePaths = $manifestNewFiles
    }
    return
}

function Test-VillageCanvasHealthy {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $healthUrl
        return $response.StatusCode -eq 200 -and $response.Content -match '"status"\s*:\s*"ok"'
    }
    catch {
        return $false
    }
}

if (Test-VillageCanvasHealthy) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $stopScript
    $deadline = (Get-Date).AddSeconds(30)
    while ((Test-VillageCanvasHealthy) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Test-VillageCanvasHealthy) {
        throw 'Village Infinite Canvas did not stop before rollback.'
    }
}

$restoredBackend = 0
$restoredFrontend = 0
$restoredAgentSkills = 0
foreach ($area in @('backend', 'frontend', 'agent-skills')) {
    $source = Join-Path $backup $area
    if (-not (Test-Path -LiteralPath $source -PathType Container)) { continue }
    $destination = if ($area -eq 'backend') {
        Join-Path $target 'runtime\env\novelvideo'
    }
    elseif ($area -eq 'frontend') {
        if (Test-Path -LiteralPath $rollbackManifestPath -PathType Leaf) {
            Join-Path $target 'frontend\dist'
        }
        else {
            # Preserve the layout of historical backups made before the
            # structured manifest; new backups mirror dist directly.
            Join-Path $target 'frontend'
        }
    }
    else {
        Join-Path $target 'agent_skills'
    }
    foreach ($file in Get-ChildItem -LiteralPath $source -Recurse -File -Force) {
        $relative = [IO.Path]::GetRelativePath($source, $file.FullName)
        $destinationFile = Join-Path $destination $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destinationFile) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destinationFile -Force
        if ($area -eq 'backend') { $restoredBackend++ }
        elseif ($area -eq 'frontend') { $restoredFrontend++ }
        else { $restoredAgentSkills++ }
    }
}

# This backup was created before the 2026-08-05 direct-model deployment.
# These files did not exist in that snapshot, so removing only this explicit
# manifest completes the rollback without touching user assets or old chunks.
$newFilesForThisBackup = @()
if ((Split-Path -Leaf $backup) -eq 'village-canvas-20260805-135244') {
    $newFilesForThisBackup = @(
        'runtime\env\novelvideo\generators\direct_image_models.py',
        'runtime\env\novelvideo\generators\direct_models.py',
        'frontend\assets\beats.lazy-kiB9gv9Z.js',
        'frontend\assets\characters.lazy-gygFeLQO.js',
        'frontend\assets\compose.lazy-CzbD4sjP.js',
        'frontend\assets\episode-empty-state-Bzz7yA6M.js',
        'frontend\assets\freezone.lazy-ClqbYcp1.js',
        'frontend\assets\icons-CYZNyVs7.js',
        'frontend\assets\index-BzwTcZgy.js',
        'frontend\assets\index-DcXesWB1.js',
        'frontend\assets\index-K9yDX1X6.css',
        'frontend\assets\media-url-GFb_tlTK.js',
        'frontend\assets\props-DSXdXfEl.js',
        'frontend\assets\save-status-BuJZiJMM.js',
        'frontend\assets\script.lazy-DD9juxf0.js',
        'frontend\assets\scripts-B4FsFoRC.js',
        'frontend\assets\sketch-colors-6M0oK4X5.js',
        'frontend\assets\superchat-panel-C_JshI9r.js',
        'frontend\assets\ThreeDDirectorDialog-BU8fXHaz.js',
        'frontend\assets\video-CjMYG7Mp.js'
    )
}
$removedNewFiles = 0
foreach ($candidate in $manifestNewFiles) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        Remove-Item -LiteralPath $candidate -Force
        $removedNewFiles++
    }
}
foreach ($relative in $newFilesForThisBackup) {
    $candidate = [IO.Path]::GetFullPath((Join-Path $target $relative))
    if (-not $candidate.StartsWith($target.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Rollback deletion escaped the target root: $relative"
    }
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        Remove-Item -LiteralPath $candidate -Force
        $removedNewFiles++
    }
}

Start-Process -FilePath $pythonExe -ArgumentList @($launchScript) -WorkingDirectory $target -WindowStyle Hidden
$deadline = (Get-Date).AddSeconds(45)
while (-not (Test-VillageCanvasHealthy) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
}
if (-not (Test-VillageCanvasHealthy)) {
    throw 'Rollback files were restored but 8784 did not become healthy.'
}

[pscustomobject]@{
    rolledBack = $true
    target = $target
    backup = $backup
    restoredBackend = $restoredBackend
    restoredFrontend = $restoredFrontend
    restoredAgentSkills = $restoredAgentSkills
    removedNewFiles = $removedNewFiles
    health = 'ok'
}
