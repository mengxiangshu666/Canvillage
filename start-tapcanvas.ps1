param(
  [string]$SidecarDir = $env:VILLAGE_AGENT_SIDECAR_DIR,
  [int]$Port = 8790
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (-not $SidecarDir) {
  $SidecarDir = Join-Path $root 'runtime\agent_sidecar\apps\agents-cli'
}

& (Join-Path $root 'scripts\agent_sidecar.ps1') -Action start -Dir $SidecarDir -Port $Port

$env:VILLAGE_AGENT_SIDECAR_URL = "http://127.0.0.1:$Port"

# The normal product launcher is authoritative: it starts the API on 8784 and
# now also starts the bundled sidecar when it is not running yet.
& (Join-Path $root '启动村长无限画布.bat')
