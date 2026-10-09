param(
    [Parameter(Mandatory)][string]$Task
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\codex_continuity_common.ps1"
Ensure-ContinuityDirectories
$TaskId = ConvertTo-TaskId $Task
$taskPath = Get-TaskPath $TaskId
$handoffPath = Get-HandoffPath $TaskId
$taskContent = Get-Content -LiteralPath $taskPath -Raw -Encoding UTF8
$handoffContent = if (Test-Path -LiteralPath $handoffPath) {
    Get-Content -LiteralPath $handoffPath -Raw -Encoding UTF8
} else {
    ""
}
$errors = New-Object System.Collections.Generic.List[string]

foreach ($heading in @("## Goal", "## Scope", "## Non-goals", "## Acceptance")) {
    if (-not (Get-SectionBody -Content $taskContent -Heading $heading)) {
        $errors.Add("Task card is missing a non-empty section: $heading")
    }
}
if ($taskContent -notmatch '(?m)^id:\s*' + [regex]::Escape($TaskId) + '\s*$') {
    $errors.Add("Task card front matter ID does not match $TaskId")
}
if ($taskContent -notmatch '(?m)^status:\s*(planned|in_progress|blocked|verified)\s*$') {
    $errors.Add("Task card status must be planned, in_progress, blocked, or verified")
}
if ($taskContent -notmatch '(?m)^- \[[ xX]\] AC-\d+:') {
    $errors.Add("Task card must contain at least one acceptance item named AC-1, AC-2, ...")
}

if (-not $handoffContent) {
    $errors.Add("Handoff file does not exist: $handoffPath")
} else {
    foreach ($heading in @("## Workspace baseline", "## Completed", "## Remaining", "## Decisions", "## Hypotheses", "## Evidence", "## Blocker")) {
        if ($handoffContent -notmatch '(?m)^' + [regex]::Escape($heading) + '\s*$') {
            $errors.Add("Handoff is missing section: $heading")
        }
    }
    if ($handoffContent -notmatch '(?s)<!-- codex:generated:start -->.*?<!-- codex:generated:end -->') {
        $errors.Add("Handoff is missing the generated checkpoint block")
    }
    if ($handoffContent -match '(?m)^- Next action:\s*(|TODO|TBD|Fill in.*)$') {
        $errors.Add("Handoff must contain a concrete next action")
    }
}

$statusMatch = [regex]::Match($taskContent, '(?m)^status:\s*(?<status>\w+)\s*$')
if ($statusMatch.Success -and $statusMatch.Groups["status"].Value -eq "verified") {
    $unchecked = @([regex]::Matches($taskContent, '(?m)^- \[ \] AC-\d+:'))
    if ($unchecked.Count -gt 0) {
        $errors.Add("Task is marked verified but still has $($unchecked.Count) unchecked acceptance item(s)")
    }
    $evidence = Get-SectionBody -Content $taskContent -Heading "## Evidence"
    if (-not $evidence -or $evidence -notmatch '(?mi)^- (Tests|Build/typecheck|Runtime|Rollback):\s*\S') {
        $errors.Add("Verified task must record evidence in the Evidence section")
    }
}

if ($errors.Count -gt 0) {
    Write-Output "Handoff verification FAILED for $TaskId"
    $errors | ForEach-Object { Write-Output "- $_" }
    exit 1
}

Write-Output "Handoff verification PASSED for $TaskId"
Write-Output "Task card: $taskPath"
Write-Output "Handoff:    $handoffPath"
