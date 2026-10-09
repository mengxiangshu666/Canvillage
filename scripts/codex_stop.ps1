param(
    [Parameter(Mandatory)][string]$Task,
    [ValidateSet("planned", "in_progress", "blocked", "verified")][string]$Status = "in_progress",
    [Parameter(Mandatory)][string]$NextAction
)

$ErrorActionPreference = "Stop"

& "$PSScriptRoot\codex_checkpoint.ps1" -Task $Task -Status $Status -NextAction $NextAction
if (-not $?) {
    throw "Checkpoint failed."
}

& "$PSScriptRoot\codex_verify_handoff.ps1" -Task $Task
if (-not $?) {
    throw "Checkpoint was written, but handoff verification failed."
}

Write-Output "Session closed for $Task. Start the next session with codex_start.ps1 -Task $Task."
