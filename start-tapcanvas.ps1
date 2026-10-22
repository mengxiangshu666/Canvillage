param(
  [string]$SidecarDir = $env:VILLAGE_AGENT_SIDECAR_DIR,
  [int]$Port = 8790
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (-not $SidecarDir) {
  throw 'Point -SidecarDir or VILLAGE_AGENT_SIDECAR_DIR at the TapCanvas apps/agents-cli directory.'
}

& (Join-Path $root 'scripts\agent_sidecar.ps1') -Action start -Dir $SidecarDir -Port $Port

$env:VILLAGE_CHAT_AGENT_BACKEND = 'tapcanvas'
$env:VILLAGE_AGENT_SIDECAR_URL = "http://127.0.0.1:$Port"

# The normal launcher exits early when 8784 is already listening.  This
# explicit launcher must restart the API so the backend switch takes effect.
$listener = Get-NetTCPConnection -LocalPort 8784 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
  & (Join-Path $root '_stop.ps1')
}

# The normal product launcher stays authoritative; this only sets the backend
# switch and lets the existing launcher start/attach the API on 8784.
& (Join-Path $root '启动村长无限画布.bat')
