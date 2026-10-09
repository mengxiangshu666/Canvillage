param(
    [string]$TaskId,
    [Parameter(Mandatory)][string]$Title,
    [string]$Goal = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\codex_continuity_common.ps1"
Ensure-ContinuityDirectories

if (-not $TaskId) {
    $numbers = @(Get-ChildItem -LiteralPath $script:TaskRoot -Filter "T-*.md" -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            if ($_.BaseName -match '^T-(\d+)') { [int]$Matches[1] }
        })
    $next = if ($numbers.Count -eq 0) { 1 } else { (($numbers | Measure-Object -Maximum).Maximum + 1) }
    $TaskId = "T-{0:D3}" -f $next
}
$TaskId = ConvertTo-TaskId $TaskId

$safeTitle = ($Title.Trim().ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
if (-not $safeTitle) { $safeTitle = "task" }
$taskPath = Join-Path $script:TaskRoot "$TaskId-$safeTitle.md"
$handoffPath = Get-HandoffPath $TaskId

if ((Test-Path -LiteralPath $taskPath) -and -not $Force) {
    throw "Task already exists: $taskPath (use -Force only when replacing it deliberately)"
}

$now = (Get-Date).ToString("o")
$goalText = if ($Goal) { $Goal.Trim() } else { "Fill in the user-visible outcome." }
$task = @"
---
id: $TaskId
title: $Title
status: planned
created: $now
updated: $now
---

# $TaskId - $Title

## Goal

$goalText

## User benefit

Describe the user-visible improvement.

## Scope

- Include the modules, interfaces, or workflows that are in scope.

## Non-goals

- State what this task deliberately does not change.

## Acceptance

- [ ] AC-1: Define the first observable acceptance criterion.
- [ ] AC-2: Define the regression or compatibility criterion.
- [ ] AC-3: Define the evidence required to call this verified.

## Evidence

- Tests:
- Build/typecheck:
- Runtime:
- Rollback:

## Risks and dependencies

- None recorded.
"@
Write-Utf8Text -Path $taskPath -Content ($task.Trim() + "`r`n")

    $handoff = @"
# Handoff $TaskId

$(Get-GeneratedHandoffBlock -TaskId $TaskId -Status "planned" -NextAction "Read the task card, inspect the current diff, and choose the smallest implementation step.")

## Completed

- [ ] No acceptance item is complete yet.

## Remaining

- [ ] Fill in the first implementation step.

## Decisions

- None recorded.

## Hypotheses

- None recorded.

## Evidence

- Tests:
- Build:
- Runtime:

## Blocker

- None.
"@
Write-Utf8Text -Path $handoffPath -Content ($handoff.Trim() + "`r`n")

Write-Output "Created task card: $taskPath"
Write-Output "Created handoff:   $handoffPath"
Write-Output "Next: edit the acceptance criteria, then run codex_verify_handoff.ps1 -Task $TaskId"
