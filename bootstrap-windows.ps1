[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

$errors = [System.Collections.Generic.List[string]]::new()

function Find-Command([string]$name) {
    return Get-Command $name -ErrorAction SilentlyContinue
}

Write-Host "Google Trends Toolkit - new machine check"
Write-Host "Repo: $root"

$python = Find-Command "python"
if (-not $python) {
    $python = Find-Command "py"
}
if (-not $python) {
    $errors.Add("Python 3.9+ not found")
} else {
    $pythonVersion = & $python.Source -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]"3.9") {
        $errors.Add("Python 3.9+ required; found $pythonVersion")
    } else {
        Write-Host "PASS Python $pythonVersion"
    }
}

$git = Find-Command "git"
if (-not $git) {
    $errors.Add("Git not found")
} else {
    $repoRoot = (& $git.Source rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
        $errors.Add("This folder is not a Git clone")
    } else {
        Write-Host "PASS Git repository"
    }

    $remote = (& $git.Source remote get-url origin 2>$null)
    if ($LASTEXITCODE -ne 0 -or $remote -notmatch "reload0981-ops/google-trends-toolkit") {
        $errors.Add("origin does not point to reload0981-ops/google-trends-toolkit")
    } else {
        Write-Host "PASS GitHub origin"
    }

    $authorName = (& $git.Source config user.name 2>$null)
    $authorEmail = (& $git.Source config user.email 2>$null)
    if (-not $authorName -or -not $authorEmail) {
        $errors.Add("Git author missing; set git config user.name and user.email")
    } else {
        Write-Host "PASS Git author: $authorName <$authorEmail>"
    }
}

$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $chrome) {
    $errors.Add("Google Chrome not found")
} else {
    Write-Host "PASS Google Chrome"
}

$gh = Find-Command "gh"
if (-not $gh) {
    $errors.Add("GitHub CLI not found; install gh and run gh auth login")
} else {
    & $gh.Source auth status *> $null
    if ($LASTEXITCODE -ne 0) {
        $errors.Add("GitHub CLI is not signed in; run gh auth login and gh auth setup-git")
    } else {
        Write-Host "PASS GitHub authentication"
    }
}

if ($python -and $errors.Count -eq 0) {
    $savedErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $auditOutput = & $python.Source -X utf8 collector/audit.py --strict 2>&1
        $auditExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedErrorAction
    }
    if ($auditExit -ne 0) {
        $auditOutput | ForEach-Object { Write-Host $_ }
        $errors.Add("Dataset structural audit failed")
    } else {
        Write-Host "PASS Dataset structural audit"
    }

    $ErrorActionPreference = "Continue"
    try {
        $testOutput = & $python.Source -X utf8 -m unittest discover -s tests -q 2>&1
        $testExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedErrorAction
    }
    if ($testExit -ne 0) {
        $testOutput | ForEach-Object { Write-Host $_ }
        $errors.Add("Unit tests failed")
    } else {
        Write-Host "PASS Unit tests"
    }
}

if ($errors.Count) {
    Write-Host ""
    Write-Host "NOT READY" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "- $_" -ForegroundColor Red }
    exit 1
}

$extensionPath = Join-Path $root "extension"
$incomingPath = Join-Path $root "incoming"
$jobsPath = Join-Path $root "extension\data\jobs.json"

Write-Host ""
Write-Host "MACHINE READY" -ForegroundColor Green
Write-Host "One-time Chrome setup still requires the user:"
Write-Host "1. Load unpacked extension from: $extensionPath"
Write-Host "2. Allow trends.google.co.th and set Chrome Downloads to: $incomingPath"
Write-Host "3. Turn off 'Ask where to save each file'"
Write-Host ""
Write-Host "Monthly use: the Agent creates $jobsPath; in Controller import that file and press Start."
Write-Host "Analysis setup: powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-analysis-windows.ps1"
