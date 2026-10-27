[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Root,
    [ValidateRange(5, 1000)][int]$Keep = 5,
    [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
$rootPath = (Resolve-Path -LiteralPath $Root).Path
$backupRoot = Join-Path $rootPath '_deploy_backups'
if (-not (Test-Path -LiteralPath $backupRoot)) { return }
$backupRootItem = Get-Item -LiteralPath $backupRoot -Force
if ($backupRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    # The portable layout keeps backups in workspace/backups via a junction.
    # Resolve it explicitly and still refuse targets outside this workspace.
    $target = @($backupRootItem.Target) | Select-Object -First 1
    if (-not $target) { throw 'Backup junction has no target.' }
    if (-not [IO.Path]::IsPathRooted($target)) {
        $target = Join-Path (Split-Path -Parent $backupRoot) $target
    }
    $resolvedTarget = (Resolve-Path -LiteralPath $target).Path
    $workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $rootPath 'workspace')).Path
    if (-not $resolvedTarget.StartsWith($workspaceRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Backup junction must resolve inside workspace: $resolvedTarget"
    }
    $backupRoot = $resolvedTarget
}
$backups = @(Get-ChildItem -LiteralPath $backupRoot -Directory -Force |
    Where-Object { $_.Name -match '^village-canvas-\d{8}-\d{6}(?:-\d{3})?$' } |
    Sort-Object Name -Descending)
$candidates = @($backups | Select-Object -Skip $Keep | Where-Object {
    $manifest = Join-Path $_.FullName 'rollback-manifest.json'
    try {
        -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
        (Test-Path -LiteralPath $manifest) -and
        ((Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).schema -eq 'village_deployment_rollback.v1')
    } catch { $false }
})
$result = [ordered]@{ retained=@($backups | Select-Object -First $Keep -ExpandProperty Name); candidates=@($candidates.Name); recycled=@() }
if ($PlanOnly) { [pscustomobject]$result; return }
if ($env:OS -ne 'Windows_NT') { throw 'Recycle Bin cleanup requires Windows.' }
Add-Type -AssemblyName Microsoft.VisualBasic
foreach ($item in $candidates) {
    $path = [IO.Path]::GetFullPath($item.FullName)
    if (-not $path.StartsWith($backupRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup outside root.' }
    try {
        [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($path,
            [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
            [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin,
            [Microsoft.VisualBasic.FileIO.UICancelOption]::ThrowException)
    } catch {
        # Some Windows Server / container sessions do not expose a Recycle Bin.
        # Do not permanently delete a rollback backup: quarantine it inside the
        # same workspace-backed root so it stays recoverable and out of the
        # active retention set.
        $quarantineRoot = Join-Path $backupRoot '_recycled'
        New-Item -ItemType Directory -Force -Path $quarantineRoot | Out-Null
        $destination = Join-Path $quarantineRoot ("{0}-{1}" -f $item.Name, (Get-Date -Format 'yyyyMMddHHmmssfff'))
        [IO.Directory]::Move($path, $destination)
    }
    $result.recycled += $item.Name
}
[pscustomobject]$result
