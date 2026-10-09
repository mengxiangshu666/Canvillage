Set-StrictMode -Version Latest

$script:ContinuityRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:AiRoot = Join-Path $script:ContinuityRoot "docs\ai"
$script:TaskRoot = Join-Path $script:AiRoot "tasks"
$script:HandoffRoot = Join-Path $script:AiRoot "handoffs"
$script:DecisionRoot = Join-Path $script:AiRoot "decisions"

function Ensure-ContinuityDirectories {
    foreach ($path in @($script:AiRoot, $script:TaskRoot, $script:HandoffRoot, $script:DecisionRoot)) {
        if (-not (Test-Path -LiteralPath $path)) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
    }
}

function ConvertTo-TaskId {
    param([Parameter(Mandatory)][string]$TaskId)

    $normalized = $TaskId.Trim().ToUpperInvariant()
    if ($normalized -notmatch '^T-\d{3,}$') {
        throw "Task ID must match T-001 or a larger numeric ID: $TaskId"
    }
    return $normalized
}

function Get-TaskPath {
    param([Parameter(Mandatory)][string]$TaskId)

    $id = ConvertTo-TaskId $TaskId
    $matches = @(Get-ChildItem -LiteralPath $script:TaskRoot -Filter "$id-*.md" -File -ErrorAction SilentlyContinue)
    if ($matches.Count -eq 0) {
        throw "Task card not found for ${id} under $script:TaskRoot"
    }
    if ($matches.Count -gt 1) {
        throw "More than one task card found for ${id}: $($matches.Name -join ', ')"
    }
    return $matches[0].FullName
}

function Get-HandoffPath {
    param([Parameter(Mandatory)][string]$TaskId)

    $id = ConvertTo-TaskId $TaskId
    return (Join-Path $script:HandoffRoot "$id-current.md")
}

function Write-Utf8Text {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Get-GitValue {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $value = & git -C $script:ContinuityRoot @Arguments 2>$null
    if ($LASTEXITCODE -ne 0) {
        return "UNAVAILABLE"
    }
    return (($value | Out-String).Trim())
}

function Get-GitStatusLines {
    $value = & git -C $script:ContinuityRoot status --short 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @("UNAVAILABLE")
    }
    return @($value | Where-Object { $_ -and $_.Trim() })
}

function Get-GeneratedHandoffBlock {
    param(
        [Parameter(Mandatory)][string]$TaskId,
        [Parameter(Mandatory)][string]$Status,
        [Parameter(Mandatory)][string]$NextAction
    )

    $now = (Get-Date).ToString("o")
    $gitHead = Get-GitValue @("rev-parse", "HEAD")
    $branch = Get-GitValue @("branch", "--show-current")
    $statusLines = @(Get-GitStatusLines)
    $dirtyCount = $statusLines.Count
    $diffStat = Get-GitValue @("diff", "--stat")
    if (-not $diffStat) {
        $diffStat = "clean or no unstaged diff"
    }
    $statusText = if ($statusLines.Count -eq 0) { "clean" } else { ($statusLines -join "`n") }

    return @"
<!-- codex:generated:start -->
- Task: $TaskId
- Status: $Status
- Updated: $now
- Next action: $NextAction

## Workspace baseline

- Root: $script:ContinuityRoot
- Branch: $branch
- Git HEAD: $gitHead
- Dirty entries: $dirtyCount

``````text
$statusText
``````

## Diff summary

``````text
$diffStat
``````
<!-- codex:generated:end -->
"@.Trim()
}

function Get-DominantNewline {
    param([AllowEmptyString()][string]$Text = "")

    # The continuity surface is committed with LF but checked out as CRLF on
    # Windows (core.autocrlf=true).  Anything that rewrites one of these files has
    # to follow that file's own convention instead of assuming either one: a
    # hard-coded `r`n appends a stray CR to an LF card, and a hard-coded `n turns a
    # CRLF card into a mixed-ending file.  Whichever terminator is in the majority
    # wins; an empty or terminator-free file defaults to CRLF, which is what a
    # Windows checkout looks like.
    $crlf = ([regex]::Matches($Text, "\r\n")).Count
    $lf = ([regex]::Matches($Text, "(?<!\r)\n")).Count
    if ($lf -gt $crlf) { return "`n" }
    return "`r`n"
}

function ConvertTo-Newline {
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$Newline
    )

    $unified = ($Text -replace "`r`n", "`n") -replace "`r", "`n"
    if ($Newline -eq "`n") { return $unified }
    return ($unified -replace "`n", "`r`n")
}

function Set-GeneratedHandoffBlock {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Block
    )

    $existing = if (Test-Path -LiteralPath $Path) {
        Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    } else {
        ""
    }
    # The here-string in Get-GeneratedHandoffBlock inherits this file's own line
    # endings (LF), so splicing it verbatim into a CRLF handoff used to leave that
    # handoff with mixed endings.  Follow the target file's convention instead.
    $newline = Get-DominantNewline $existing
    $Block = ConvertTo-Newline -Text $Block -Newline $newline
    $pattern = '(?s)<!-- codex:generated:start -->.*?<!-- codex:generated:end -->'
    if ($existing -match $pattern) {
        $content = [regex]::Replace($existing, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($match) $Block }, 1)
    } elseif ($existing) {
        $content = "$Block${newline}${newline}$existing"
    } else {
        $content = "$Block${newline}${newline}## Completed${newline}${newline}- [ ] Fill in completed acceptance items and evidence.${newline}${newline}## Remaining${newline}${newline}- [ ] Fill in remaining work.${newline}${newline}## Decisions${newline}${newline}- None recorded.${newline}${newline}## Hypotheses${newline}${newline}- None recorded.${newline}${newline}## Evidence${newline}${newline}- Tests:${newline}- Build:${newline}- Runtime:${newline}${newline}## Blocker${newline}${newline}- None.${newline}"
    }
    Write-Utf8Text -Path $Path -Content ($content.Trim() + $newline)
}

function Get-SectionBody {
    param(
        [Parameter(Mandatory)][string]$Content,
        [Parameter(Mandatory)][string]$Heading
    )

    $escaped = [regex]::Escape($Heading)
    $match = [regex]::Match($Content, "(?ms)^$escaped\s*$\r?\n(?<body>.*?)(?=^##\s|\z)")
    if (-not $match.Success) {
        return ""
    }
    return $match.Groups["body"].Value.Trim()
}
