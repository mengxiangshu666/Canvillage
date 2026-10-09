[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dataDir = Join-Path $root '项目资产'
$stateDir = Join-Path $dataDir 'state'
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$canonical = Join-Path $root '启动村长无限画布.bat'
$launchers = @($canonical) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }

function Read-CredentialFromLauncher {
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$Name
    )
    $pattern = '(?im)^\s*set\s+"' + [regex]::Escape($Name) + '=(.*?)"\s*$'
    $match = [regex]::Match($Text, $pattern)
    if (-not $match.Success) { return '' }
    return $match.Groups[1].Value.Trim()
}

function Save-SecretOnce {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )
    if (-not $Value) { return }
    $path = Join-Path $stateDir $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        [IO.File]::WriteAllText($path, $Value + "`n", [Text.UTF8Encoding]::new($false))
    }
}

$canonicalText = [IO.File]::ReadAllText($canonical, [Text.Encoding]::UTF8)
Save-SecretOnce -Name 'gateway-api-key.txt' -Value (Read-CredentialFromLauncher -Text $canonicalText -Name 'VILLAGE_CANVAS_HERMES_API_KEY')

$gatewayLoad = @'
set "VILLAGE_CANVAS_HERMES_API_KEY="
if exist "%DATA_DIR%\state\gateway-api-key.txt" (
  set /p VILLAGE_CANVAS_HERMES_API_KEY=<"%DATA_DIR%\state\gateway-api-key.txt"
)
'@.TrimEnd()

foreach ($path in $launchers) {
    $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    $text = [regex]::Replace(
        $text,
        '(?im)^\s*set\s+"VILLAGE_CANVAS_HERMES_API_KEY=.*?"\s*$',$gatewayLoad
    )
    [IO.File]::WriteAllText($path, $text, [Text.UTF8Encoding]::new($false))
}

# These archived launchers are not runtime inputs. Keep the files for provenance,
# but remove credential material from the tracked copies.
$legacyRoot = Join-Path $root 'tools\legacy\bak-20260721'
if (Test-Path -LiteralPath $legacyRoot -PathType Container) {
    Get-ChildItem -LiteralPath $legacyRoot -File -Filter '*.bat*' | ForEach-Object {
        $text = [IO.File]::ReadAllText($_.FullName, [Text.Encoding]::UTF8)
        $text = [regex]::Replace($text, '(?im)^\s*set\s+"(?:VILLAGE_CANVAS_HERMES_API_KEY|FREEZONE_VISION_FALLBACK_API_KEY)=.*?"\s*$','set "VILLAGE_CANVAS_HERMES_API_KEY="')
        [IO.File]::WriteAllText($_.FullName, $text, [Text.UTF8Encoding]::new($false))
    }
}

Write-Output "local gateway credentials moved to 项目资产\state; tracked launchers redacted"
