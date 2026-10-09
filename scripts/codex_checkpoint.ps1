param(
    [Parameter(Mandatory)][string]$Task,
    [ValidateSet("planned", "in_progress", "blocked", "verified")][string]$Status = "in_progress",
    [Parameter(Mandatory)][string]$NextAction
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\codex_continuity_common.ps1"
Ensure-ContinuityDirectories
$TaskId = ConvertTo-TaskId $Task
$taskPath = Get-TaskPath $TaskId
$handoffPath = Get-HandoffPath $TaskId

if ([string]::IsNullOrWhiteSpace($NextAction)) {
    throw "NextAction must be a concrete action, not an empty summary."
}

$taskContent = Get-Content -LiteralPath $taskPath -Raw -Encoding UTF8
$now = (Get-Date).ToString("o")
# Follow the card's own newline convention.  Appending a hard-coded `r`n is what
# left an LF card (T-001) carrying a single stray CR.
$newline = Get-DominantNewline $taskContent
# Match up to (but never including) the line terminator: `.` matches `\r` while `$`
# anchors before `\n`, so `^key:.*$` on a CRLF file consumes the CR and rewrites
# that line as lone-LF — a CRLF card comes back with mixed endings.
$taskContent = [regex]::Replace($taskContent, '(?m)^status:[^\r\n]*', "status: $Status", 1)
$taskContent = [regex]::Replace($taskContent, '(?m)^updated:[^\r\n]*', "updated: $now", 1)
Write-Utf8Text -Path $taskPath -Content ($taskContent.Trim() + $newline)

$block = Get-GeneratedHandoffBlock -TaskId $TaskId -Status $Status -NextAction $NextAction.Trim()
Set-GeneratedHandoffBlock -Path $handoffPath -Block $block

Write-Output "Checkpoint written: $handoffPath"
Write-Output "Task status: $Status"
Write-Output "Next action: $($NextAction.Trim())"
