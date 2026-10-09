param(
    [Parameter(Mandatory)][string]$Task,
    [int]$MaxChars = 18000,
    [string]$OutFile
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\codex_continuity_common.ps1"
Ensure-ContinuityDirectories
$TaskId = ConvertTo-TaskId $Task
$taskPath = Get-TaskPath $TaskId
$handoffPath = Get-HandoffPath $TaskId

function Read-Bounded {
    param([Parameter(Mandatory)][string]$Path)
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($content.Length -le $MaxChars) {
        return $content.Trim()
    }
    return ($content.Substring(0, $MaxChars).Trim() + "`r`n`r`n[TRUNCATED: inspect the file directly before proceeding]")
}

$taskText = Read-Bounded $taskPath
$handoffText = if (Test-Path -LiteralPath $handoffPath) {
    Read-Bounded $handoffPath
} else {
    "No handoff exists. Run codex_checkpoint.ps1 before continuing."
}
$gitBranch = Get-GitValue @("branch", "--show-current")
$gitHead = Get-GitValue @("rev-parse", "HEAD")
$statusLines = Get-GitStatusLines
$statusText = if ($statusLines.Count -eq 0) { "clean" } else { ($statusLines -join "`n") }
$decisions = @(Get-ChildItem -LiteralPath $script:DecisionRoot -Filter "*.md" -File -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -ExpandProperty Name)
$decisionText = if ($decisions.Count -eq 0) { "none" } else { ($decisions -join ", ") }

$packet = @"
# Codex Resume Packet: $TaskId

Use this packet as the bounded startup context for a new session.

## Instructions

1. Read the task card and handoff below.
2. Verify the current Git state before changing files.
3. Treat confirmed facts and evidence as authoritative; treat hypotheses as unverified.
4. Work only on $TaskId.
5. Execute the single next action from the handoff, then checkpoint again.

## Current Git baseline

- Root: $script:ContinuityRoot
- Branch: $gitBranch
- HEAD: $gitHead

``````text
$statusText
``````

## Durable decisions available

$decisionText

## Task card

``````markdown
$taskText
``````

## Current handoff

``````markdown
$handoffText
``````

## First command

Run the repository's read-only project snapshot or the narrowest relevant test before making an assumption about runtime state.
"@.Trim() + "`r`n"

if ($OutFile) {
    $resolvedOutFile = if ([System.IO.Path]::IsPathRooted($OutFile)) {
        [System.IO.Path]::GetFullPath($OutFile)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutFile))
    }
    Write-Utf8Text -Path $resolvedOutFile -Content $packet
    Write-Output "Resume packet written: $resolvedOutFile"
} else {
    Write-Output $packet
}
