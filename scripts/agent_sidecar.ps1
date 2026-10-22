# Optional TapCanvas sidecar launcher for local A/B runs.
#
# This is NOT part of the default product startup.  The Village Canvas API keeps
# working without the sidecar; only `VILLAGE_CHAT_AGENT_BACKEND=tapcanvas` routes
# chat turns through it.  The external TapCanvas checkout stays outside the
# repository; set VILLAGE_AGENT_SIDECAR_DIR to its apps/agents-cli directory.
#
# Every start verifies the isolation patch before launching: shell, filesystem,
# web, workflow, subagent and file-editor tools must all be disabled.  A sidecar
# that can run local commands is not an acceptable agent brain.

param(
  [ValidateSet('start', 'stop', 'status')]
  [string]$Action = 'status',
  [string]$Dir = $env:VILLAGE_AGENT_SIDECAR_DIR,
  [int]$Port = 8790
)

$ErrorActionPreference = 'Stop'
$root = (Split-Path -Parent $PSScriptRoot).TrimEnd('\')
$stateDir = Join-Path $root 'workspace\agent-sidecar'
$pidFile = Join-Path $stateDir "sidecar-$Port.pid"
$outLog = Join-Path $stateDir "sidecar-$Port.out.log"
$errLog = Join-Path $stateDir "sidecar-$Port.err.log"

function Write-Status {
  param([hashtable]$Payload)
  $Payload | ConvertTo-Json -Compress
}

function Test-IsSidecarProcess {
  param([int]$ProcessId)
  if ($ProcessId -le 0) { return $false }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $proc) { return $false }
  return ([string]$proc.CommandLine -match 'dist[\\/]cli[\\/]index\.js')
}

function Get-RequiredIsolationIds {
  return @(
    'tool-bash',
    'tool-pwsh',
    'tool-jobs',
    'tool-fs',
    'tool-fs-search',
    'tool-web',
    'tool-workflow',
    'tool-ralph',
    'tool-str-replace-editor',
    'tool-subagent',
    'tool-subagent-fork',
    'tool-subagent-control',
    'tool-subagent-list-agents',
    'tool-subagent-report'
  )
}

function Assert-IsolationPatch {
  param([string]$SidecarDir)
  $patch = Join-Path $SidecarDir 'harness\tapcanvas.patch.yml'
  if (-not (Test-Path -LiteralPath $patch -PathType Leaf)) {
    throw "isolation patch missing: $patch"
  }
  $text = Get-Content -LiteralPath $patch -Raw
  $missing = @()
  foreach ($id in Get-RequiredIsolationIds) {
    $pattern = "(?ms)^- id:\s*$([regex]::Escape($id))\s*\r?\n(?:(?!^- id:).)*?^\s*disabled:\s*true"
    if ($text -notmatch $pattern) { $missing += $id }
  }
  if ($missing.Count -gt 0) {
    throw "sidecar isolation patch does not disable: $($missing -join ', ')"
  }
}

switch ($Action) {
  'status' {
    $running = $false
    $pidValue = 0
    if (Test-Path -LiteralPath $pidFile) {
      $raw = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)
      if ($raw -and $raw.Trim() -match '^\d+$') {
        $pidValue = [int]$raw.Trim()
        $running = Test-IsSidecarProcess -ProcessId $pidValue
      }
    }
    $healthy = $false
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
      $healthy = [bool]$health.ok
    } catch { $healthy = $false }
    Write-Status @{ running = $running; healthy = $healthy; pid = $pidValue; port = $Port }
  }
  'stop' {
    $stopped = $false
    if (Test-Path -LiteralPath $pidFile) {
      $raw = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)
      if ($raw -and $raw.Trim() -match '^\d+$') {
        $pidValue = [int]$raw.Trim()
        if (Test-IsSidecarProcess -ProcessId $pidValue) {
          & taskkill.exe /PID $pidValue /T /F | Out-Null
          $stopped = $true
        }
      }
      Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Status @{ stopped = $stopped; port = $Port }
  }
  'start' {
    if (-not $Dir) {
      throw 'VILLAGE_AGENT_SIDECAR_DIR is not set; point it at the TapCanvas apps/agents-cli directory'
    }
    $sidecarDir = (Resolve-Path -LiteralPath $Dir).Path
    $entry = Join-Path $sidecarDir 'dist\cli\index.js'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
      throw "sidecar build not found: $entry (run the TapCanvas build first)"
    }
    Assert-IsolationPatch -SidecarDir $sidecarDir
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
      throw 'node is not on PATH'
    }
    if (Test-Path -LiteralPath $pidFile) {
      $raw = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)
      if ($raw -and $raw.Trim() -match '^\d+$' -and (Test-IsSidecarProcess -ProcessId ([int]$raw.Trim()))) {
        Write-Status @{ running = $true; pid = [int]$raw.Trim(); port = $Port }
        return
      }
    }
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $process = Start-Process -FilePath 'node' `
      -ArgumentList @('dist/cli/index.js', 'serve', '--host', '127.0.0.1', '--port', "$Port") `
      -WorkingDirectory $sidecarDir `
      -WindowStyle Hidden `
      -RedirectStandardOutput $outLog `
      -RedirectStandardError $errLog `
      -PassThru
    $process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
      Start-Sleep -Milliseconds 400
      try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($health.ok) {
          Write-Status @{ running = $true; healthy = $true; pid = $process.Id; port = $Port }
          return
        }
      } catch { }
    }
    throw "sidecar did not become healthy on port $Port; see $errLog"
  }
}
