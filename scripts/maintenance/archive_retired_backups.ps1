[CmdletBinding()]
param(
    [string]$RepositoryRoot = '',
    [string]$ArchiveRoot = '',
    [switch]$PlanOnly,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
} else { (Resolve-Path -LiteralPath $RepositoryRoot).Path }

if (-not (Test-Path -LiteralPath (Join-Path $repo '.git') -PathType Container)) {
    throw "Not a Village Infinite Canvas repository: $repo"
}
if ([string]::IsNullOrWhiteSpace($ArchiveRoot)) {
    throw 'ArchiveRoot is required; pass the external, operator-approved archive directory explicitly.'
}
$archiveBase = (Resolve-Path -LiteralPath $ArchiveRoot).Path
$deploySource = Join-Path $repo 'workspace\backups\deploy'
$stateSource = Join-Path $repo '项目资产\state\local\backups'
foreach ($path in @($deploySource, $stateSource)) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "Archive source missing: $path" }
}

$today = Get-Date -Format 'yyyyMMdd'
$all = @(Get-ChildItem -LiteralPath $deploySource -Directory -Force |
    Where-Object { $_.Name -match '^village-canvas-(\d{8})-(\d{6})$' })
$groups = $all | Group-Object { [regex]::Match($_.Name, '^village-canvas-(\d{8})').Groups[1].Value }
$keep = [Collections.Generic.HashSet[string]]::new()
foreach ($group in $groups) {
    if ($group.Name -eq $today) {
        foreach ($item in $group.Group) { [void]$keep.Add($item.FullName) }
        continue
    }
    $capable = @($group.Group |
        Where-Object {
            (Test-Path -LiteralPath (Join-Path $_.FullName 'backend') -PathType Container) -or
            (Test-Path -LiteralPath (Join-Path $_.FullName 'frontend') -PathType Container)
        } | Sort-Object Name -Descending)
    if ($capable.Count -gt 0) { [void]$keep.Add($capable[0].FullName) }
}
$deployCandidates = @($all | Where-Object { -not $keep.Contains($_.FullName) })
$stateEntries = @(Get-ChildItem -LiteralPath $stateSource -Force)

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$batch = Join-Path $archiveBase ('20260906-project-cleanup-' + $stamp)
$deployTarget = Join-Path $batch 'deploy-snapshots'
$stateTarget = Join-Path $batch 'state-backups'
$items = [Collections.Generic.List[object]]::new()
function Add-Files([string]$Source, [string]$Destination, [string]$Category) {
    foreach ($file in @(Get-ChildItem -LiteralPath $Source -Recurse -File -Force)) {
        $relative = $file.FullName.Substring($Source.Length + 1)
        $items.Add([pscustomobject]@{
            Source = $file.FullName; Destination = Join-Path $Destination $relative
            Category = $Category; Relative = $relative; Bytes = [int64]$file.Length
        })
    }
}
foreach ($directory in $deployCandidates) { Add-Files $directory.FullName (Join-Path $deployTarget $directory.Name) 'deploy-snapshot' }
foreach ($entry in $stateEntries) {
    if ($entry.PSIsContainer) { Add-Files $entry.FullName (Join-Path $stateTarget $entry.Name) 'state-backup' }
    else {
        $items.Add([pscustomobject]@{ Source=$entry.FullName; Destination=Join-Path $stateTarget $entry.Name
            Category='state-backup'; Relative=$entry.Name; Bytes=[int64]$entry.Length })
    }
}
$totalBytes = if ($items.Count -eq 0) { [int64]0 } else {
    [int64](($items | Measure-Object -Property Bytes -Sum).Sum)
}
Write-Host ("deploy_candidates={0}; state_entries={1}; files={2}; bytes={3:N0}" -f $deployCandidates.Count, $stateEntries.Count, $items.Count, $totalBytes)
if ($PlanOnly -or -not $Execute) { Write-Host 'PlanOnly: no files changed.'; exit 0 }

if ($items.Count -eq 0) { Write-Host 'Nothing to archive.'; exit 0 }
New-Item -ItemType Directory -Force -Path $deployTarget, $stateTarget | Out-Null
foreach ($item in $items) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $item.Destination) | Out-Null
    [IO.File]::Copy($item.Source, $item.Destination, $true)
}
$verified = [Collections.Generic.List[object]]::new()
foreach ($item in $items) {
    $destinationFile = Get-Item -LiteralPath $item.Destination -Force
    if ($destinationFile.Length -ne $item.Bytes) { throw "Length mismatch: $($item.Relative)" }
    $sourceHash = (Get-FileHash -LiteralPath $item.Source -Algorithm SHA256).Hash.ToLowerInvariant()
    $destinationHash = (Get-FileHash -LiteralPath $item.Destination -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sourceHash -ne $destinationHash) { throw "Hash mismatch: $($item.Relative)" }
    $verified.Add([ordered]@{ category=$item.Category; source=$item.Source; destination=$item.Destination
        bytes=$item.Bytes; sha256=$destinationHash })
}
$manifest = [ordered]@{
    schema='village_canvas.retired_backup_archive.v1'; created_at=(Get-Date).ToUniversalTime().ToString('o')
    repository=$repo; archive_batch=$batch
    policy=[ordered]@{ deploy_keep='today_all_plus_each_earlier_day_latest_snapshot_with_backend_or_frontend'
        state='all_entries_under_project_assets_state_local_backups'; copy_then_hash_then_remove=$true }
    # Set-StrictMode throws on a property of an empty array, so project explicitly.
    deploy_candidates=@($deployCandidates | ForEach-Object { $_.Name })
    state_entries=@($stateEntries | ForEach-Object { $_.Name })
    file_count=$verified.Count; total_bytes=$totalBytes; files=@($verified)
}
$manifestPath = Join-Path $batch 'manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
foreach ($directory in $deployCandidates) { Remove-Item -LiteralPath $directory.FullName -Recurse -Force }
foreach ($entry in $stateEntries) { Remove-Item -LiteralPath $entry.FullName -Recurse -Force }
$remaining = @($items | Where-Object { Test-Path -LiteralPath $_.Source })
if ($remaining.Count -gt 0) { throw "Source files remain after archive: $($remaining.Count)" }
Write-Host ("ARCHIVE_OK batch={0}; manifest={1}; files={2}; bytes={3:N0}" -f $batch, $manifestPath, $verified.Count, $totalBytes)
