[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageRoot,
    [switch]$VerifyPayloadHashes,
    [switch]$VerifyPythonImport
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$PackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
$required = @(
    '启动村长无限画布.bat',
    '启动村长无限画布.vbs',
    '_stop.ps1',
    'village_canvas_launch.py',
    'village_canvas_run_api.py',
    'village_canvas_open_when_ready.py',
    'village_canvas_rotate_log.py',
    'src\novelvideo\api\app.py',
    'frontend\dist\index.html',
    'frontend\dist\version.json',
    'runtime\python\python.exe',
    'runtime\python\pythonw.exe',
    'runtime\ffmpeg',
    'runtime\node',
    'agent_skills\village-canvas\SKILL.md',
    '项目资产',
    'BUILD_INFO.json',
    'MANIFEST.json',
    'SHA256SUMS',
    'README-Portable.txt'
)

$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $PackageRoot $_)) })
if ($missing.Count -gt 0) {
    throw "Release package is missing required items:`n$($missing -join "`n")"
}

$forbiddenRoots = @(
    '.git', '.venv', 'node_modules', '.pnpm-store', '_task_backups',
    '_deploy_backups', '_integration_backups', 'state', 'output'
)
$presentForbidden = @($forbiddenRoots | Where-Object { Test-Path -LiteralPath (Join-Path $PackageRoot $_) })
if ($presentForbidden.Count -gt 0) {
    throw "Release package contains forbidden development/runtime roots:`n$($presentForbidden -join "`n")"
}

$staleHermesPaths = @(
    (Join-Path $PackageRoot 'runtime\.hermes'),
    (Join-Path $PackageRoot 'runtime\hermes')
)
$presentStaleHermes = @($staleHermesPaths | Where-Object { Test-Path -LiteralPath $_ })
if ($presentStaleHermes.Count -gt 0) {
    throw "Release package contains stale Hermes runtime assets:`n$($presentStaleHermes -join "`n")"
}

$buildInfo = Get-Content -LiteralPath (Join-Path $PackageRoot 'BUILD_INFO.json') -Raw | ConvertFrom-Json
$manifest = Get-Content -LiteralPath (Join-Path $PackageRoot 'MANIFEST.json') -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$buildInfo.packageName)) {
    throw 'BUILD_INFO.json has no packageName.'
}
if (@($manifest.files).Count -le 0) {
    throw 'MANIFEST.json contains no payload files.'
}

$manifestPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($entry in @($manifest.files)) {
    [void]$manifestPaths.Add(([string]$entry.path).Replace('\', '/'))
}
$privateProjectAssetsIncluded = [bool]$buildInfo.privateProjectAssetsIncluded
$ignoredMutableRuntimeFiles = 0
$ignoredGeneratedPythonCacheFiles = 0
$allowedUnmanifested = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
[void]$allowedUnmanifested.Add('MANIFEST.json')
[void]$allowedUnmanifested.Add('SHA256SUMS')
$actualPayloadPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($file in Get-ChildItem -LiteralPath $PackageRoot -Recurse -File -Force) {
    $relative = $file.FullName.Substring($PackageRoot.Length).TrimStart([char[]]'\\/').Replace('\', '/')
    # A default package starts with an empty local data root. Later runtime
    # state must not invalidate validation of the static release payload.
    if (-not $privateProjectAssetsIncluded -and $relative.StartsWith('项目资产/', [StringComparison]::OrdinalIgnoreCase)) {
        $ignoredMutableRuntimeFiles++
        continue
    }
    # Direct Python invocations can create caches beside bundled modules when
    # PYTHONPYCACHEPREFIX is absent. They are generated state, never payload.
    if ($relative -match '(^|/)(__pycache__/.*|.*\.(pyc|pyo)$)') {
        $ignoredGeneratedPythonCacheFiles++
        continue
    }
    [void]$actualPayloadPaths.Add($relative)
}
$unexpectedPayloadFiles = @($actualPayloadPaths | Where-Object {
    -not $manifestPaths.Contains($_) -and -not $allowedUnmanifested.Contains($_)
})
if ($unexpectedPayloadFiles.Count -gt 0) {
    throw "Release package contains unmanifested files:`n$($unexpectedPayloadFiles -join "`n")"
}
$missingManifestFiles = @($manifestPaths | Where-Object { -not $actualPayloadPaths.Contains($_) })
if ($missingManifestFiles.Count -gt 0) {
    throw "Release manifest references missing files:`n$($missingManifestFiles -join "`n")"
}

$hashesChecked = 0
if ($VerifyPayloadHashes) {
    foreach ($entry in @($manifest.files)) {
        $relative = [string]$entry.path
        $path = Join-Path $PackageRoot ($relative -replace '/', '\\')
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Manifest payload file is missing: $relative"
        }
        $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne [string]$entry.sha256) {
            throw "Manifest SHA256 mismatch: $relative"
        }
        $hashesChecked++
    }
}

$pythonImportVerified = $false
if ($VerifyPythonImport) {
    $python = Join-Path $PackageRoot 'runtime\python\python.exe'
    $pathEntries = @(
        (Join-Path $PackageRoot 'src'),
        (Join-Path $PackageRoot 'runtime\env'),
        (Join-Path $PackageRoot 'runtime\env\win32'),
        (Join-Path $PackageRoot 'runtime\env\win32\lib'),
        (Join-Path $PackageRoot 'runtime\env\Pythonwin')
    )
    $saved = @{}
    $overrides = @{
        PYTHONPATH = ($pathEntries -join ';')
        PATH = ((Join-Path $PackageRoot 'runtime\ffmpeg') + ';' + (Join-Path $PackageRoot 'runtime\node') + ';' + $env:PATH)
        NOVELVIDEO_DATA_ROOT = (Join-Path $PackageRoot '项目资产')
        NOVELVIDEO_STATE_DIR = (Join-Path $PackageRoot '项目资产\state')
        NOVELVIDEO_OUTPUT_DIR = (Join-Path $PackageRoot '项目资产\output')
        NOVELVIDEO_RUNTIME_DIR = (Join-Path $PackageRoot '项目资产\runtime')
        VILLAGE_CANVAS_FRONTEND_DIST = (Join-Path $PackageRoot 'frontend\dist')
        VILLAGE_CANVAS_AGENT_SKILLS_DIR = (Join-Path $PackageRoot 'agent_skills')
        VILLAGE_CANVAS_DIRECT_MODELS_ONLY = '1'
        VILLAGE_CANVAS_REQUIRE_VERIFIED_DIRECT_MODELS = '1'
    }
    foreach ($key in $overrides.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $overrides[$key], 'Process')
    }
    try {
        $probe = @'
import json
import os
from pathlib import Path

from novelvideo import config
from novelvideo.agent_tools import build_native_registry

root = Path(os.environ['NOVELVIDEO_DATA_ROOT']).resolve().parent
result = {
    'data_root_is_package_local': Path(config.DATA_ROOT).resolve().is_relative_to(root),
    'frontend_exists': Path(os.environ['VILLAGE_CANVAS_FRONTEND_DIST']).joinpath('index.html').is_file(),
    'native_skill_root_exists': Path(os.environ['VILLAGE_CANVAS_AGENT_SKILLS_DIR']).is_dir(),
    'native_skill_tool_exists': 'skill' in {tool.name for tool in build_native_registry().list_tools()},
}
if not all(result.values()):
    raise SystemExit(json.dumps(result, ensure_ascii=False))
print(json.dumps(result, ensure_ascii=False))
'@
        $priorErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $probeOutput = & $python -B -c $probe 2>&1
            $probeExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $priorErrorActionPreference
        }
        if ($probeExitCode -ne 0) {
            throw "Portable Python import probe failed:`n$($probeOutput -join "`n")"
        }
        $pythonImportVerified = $true
    }
    finally {
        foreach ($key in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process')
        }
    }
}

[pscustomobject]@{
    packageRoot = $PackageRoot
    packageName = [string]$buildInfo.packageName
    version = [string]$buildInfo.packageVersion
    status = [string]$buildInfo.status
    sourceDirty = [bool]$buildInfo.sourceDirty
    frontendBuildId = [string]$buildInfo.frontendBuildId
    manifestFiles = @($manifest.files).Count
    ignoredMutableRuntimeFiles = $ignoredMutableRuntimeFiles
    ignoredGeneratedPythonCacheFiles = $ignoredGeneratedPythonCacheFiles
    verifiedPayloadHashes = $hashesChecked
    verifiedPythonImport = $pythonImportVerified
    result = 'ok'
} | ConvertTo-Json -Depth 3
