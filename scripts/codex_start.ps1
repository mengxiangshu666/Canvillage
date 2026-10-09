param(
    [Parameter(Mandatory)][string]$Task,
    [int]$MaxChars = 18000,
    [string]$OutFile
)

$ErrorActionPreference = "Stop"

& "$PSScriptRoot\codex_verify_handoff.ps1" -Task $Task
if (-not $?) {
    throw "Handoff verification failed. Fix the task card or handoff before starting a new session."
}

$resumeArgs = @{
    Task = $Task
    MaxChars = $MaxChars
}
if ($OutFile) {
    $resumeArgs.OutFile = $OutFile
}
& "$PSScriptRoot\codex_resume.ps1" @resumeArgs
if (-not $?) {
    throw "Resume packet generation failed."
}
