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
if ((Get-Item -LiteralPath $backupRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'Backup root must not be a reparse point.'
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
    [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($path,
        [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
        [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin,
        [Microsoft.VisualBasic.FileIO.UICancelOption]::ThrowException)
    $result.recycled += $item.Name
}
[pscustomobject]$result
