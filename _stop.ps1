# 村长无限画布 portable - stop helper: terminate all processes belonging to THIS package only.
# taskkill /T kills the whole tree (ffmpeg/node/world children spawned by the API python).
#
# Three passes, because the launcher path is not the only way the API gets started:
#   1. the PID owning port 8784 - survives any interpreter path or relative-argv launch
#   2. the recorded PID file      - covers a listener that has not bound the port yet
#   3. process-tree sweep         - catches orphans (ffmpeg/node/gateway) under runtime\
#
# Every pass matches on the executable and the entrypoint as an ARGUMENT. Matching the
# command line as free text would hit any shell whose command string merely mentions the
# entrypoint name - including the shell running this script - and kill it.
$ErrorActionPreference = 'Continue'
$root = (Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\')
$apiPort = 8784
$killed = 0
$seen = New-Object 'System.Collections.Generic.HashSet[int]'

function Stop-PackageProcessTree {
  param([int]$ProcessId)
  if ($ProcessId -le 0) { return $false }
  if (-not $seen.Add($ProcessId)) { return $false }   # already stopped in an earlier pass
  if ($ProcessId -eq $PID) { return $false }          # never kill this script
  # Native taskkill may write "process not found" after a race; never treat as fatal.
  & cmd.exe /c "taskkill /PID $ProcessId /T /F >nul 2>nul" | Out-Null
  return $true
}

function Test-IsPackagePython {
  param($Process)
  # Only python-family executables count: a shell whose command string mentions the
  # entrypoint is not the API. The entrypoint must appear as a real argv element.
  if ($null -eq $Process) { return $false }
  if ($Process.Name -notmatch '^python(w)?\.exe$') { return $false }
  $exe = [string]$Process.ExecutablePath
  if ($exe -like "$root\runtime\*" -or $exe -like "$root\newapi\*") { return $true }
  return [bool]($Process.CommandLine -match '(^|[\s"''])village_canvas_run_api\.py([\s"''`]|$)')
}

# Pass 1: whoever holds the API port is this package by construction, but confirm the
# process really is the package API too, so a foreign process squatting on 8784 survives.
$portOwners = @(
  Get-NetTCPConnection -LocalPort $apiPort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
)
foreach ($ownerPid in @($portOwners)) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
  if ($null -eq $proc) { continue }
  if ((Test-IsPackagePython $proc) -or ([string]$proc.ExecutablePath -like "$root\runtime\*")) {
    if (Stop-PackageProcessTree -ProcessId ([int]$ownerPid)) { $killed++ }
  }
}

# Pass 2: the PID file run_api.py writes at startup. It records the API python even when
# the process has not bound the port yet, which pass 1 cannot see.
$dataDir = if ($env:NOVELVIDEO_DATA_ROOT) { $env:NOVELVIDEO_DATA_ROOT } else { Join-Path $root '项目资产' }
$pidFile = Join-Path $dataDir 'logs\api.pid'
if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
  $recorded = (Get-Content -LiteralPath $pidFile -Raw -ErrorAction SilentlyContinue)
  if ($null -ne $recorded) { $recorded = $recorded.Trim() }
  if ($recorded -match '^\d+$') {
    # Stale PID files are normal (a crash leaves the old value); confirm the process is
    # still ours before killing so a recycled PID never takes out someone else.
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$recorded" -ErrorAction SilentlyContinue
    if ((Test-IsPackagePython $proc) -or ([string]$proc.ExecutablePath -like "$root\runtime\*")) {
      if (Stop-PackageProcessTree -ProcessId ([int]$recorded)) { $killed++ }
    }
  }
}

# Pass 3: any package-owned process still alive - python-family executable under runtime\,
# or running the API/CLI entrypoint as an argument. Catches orphans whose parent died.
$owned = Get-CimInstance Win32_Process |
  Where-Object {
    (Test-IsPackagePython $_) -or
    ([string]$_.ExecutablePath -like "$root\runtime\*") -or
    ([string]$_.ExecutablePath -like "$root\newapi\*")
  }
foreach ($p in @($owned)) {
  if (Stop-PackageProcessTree -ProcessId ([int]$p.ProcessId)) { $killed++ }
}

if ($killed -gt 0) {
  Write-Host ("村长无限画布 stopped: " + $killed + " process tree(s).")
} else {
  Write-Host "村长无限画布 is not running."
}
