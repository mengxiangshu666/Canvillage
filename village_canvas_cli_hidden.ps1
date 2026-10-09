[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArguments
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "runtime\python\python.exe"
$entry = Join-Path $root "village_canvas_cli.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    [Console]::Error.WriteLine("Portable Village Canvas CLI runtime is missing.")
    exit 1
}
if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
    [Console]::Error.WriteLine("Village Canvas CLI entrypoint is missing.")
    exit 1
}

# This wrapper is launched with powershell.exe -WindowStyle Hidden.  The child
# inherits stdin/stdout/stderr, so JSON and stdio transports keep their contract
# without creating a Python console window.
& $python $entry @CliArguments
exit $LASTEXITCODE
