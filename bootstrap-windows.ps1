[CmdletBinding()]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$analysisBootstrap = Join-Path $root "scripts\bootstrap-analysis-windows.ps1"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
Set-Location -LiteralPath $root

$errors = [System.Collections.Generic.List[string]]::new()

function Find-Command([string]$name) {
    return Get-Command $name -ErrorAction SilentlyContinue
}

function Run-Check([string]$executable, [string[]]$arguments) {
    $savedErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = @(& $executable @arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedErrorAction
    }
    return [pscustomobject]@{ Output = $output; ExitCode = $exitCode }
}

Write-Host "Google Trends Toolkit - new machine setup"
Write-Host "Repo: $root"

try {
    if ($Python) {
        & $analysisBootstrap -Python $Python -NoReadyBanner
    } else {
        & $analysisBootstrap -NoReadyBanner
    }
} catch {
    $errors.Add("Analysis environment bootstrap failed: $($_.Exception.Message)")
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

if ($errors.Count -eq 0) {
    $audit = Run-Check $venvPython @("-X", "utf8", "collector/audit.py", "--strict")
    if ($audit.ExitCode -ne 0) {
        $audit.Output | ForEach-Object { Write-Host $_ }
        $errors.Add("Dataset structural audit failed")
    } else {
        Write-Host "PASS Dataset structural audit"
    }

    $siteCheck = Run-Check $venvPython @("-X", "utf8", "collector/build_site_data.py", "--check")
    if ($siteCheck.ExitCode -ne 0) {
        $siteCheck.Output | ForEach-Object { Write-Host $_ }
        $errors.Add("Generated site data check failed")
    } else {
        Write-Host "PASS Generated site data check"
    }

    $analysisAudit = Run-Check $venvPython @("-X", "utf8", "-m", "analysis.build", "--audit")
    if ($analysisAudit.ExitCode -ne 0) {
        $analysisAudit.Output | ForEach-Object { Write-Host $_ }
        $errors.Add("Analytical output audit failed")
    } else {
        Write-Host "PASS Analytical output audit"
    }

    $tests = Run-Check $venvPython @("-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v")
    $analysisSkips = @($tests.Output | Where-Object { ([string]$_) -match "test_analysis_pipeline.*skipped" })
    if ($tests.ExitCode -ne 0) {
        $tests.Output | ForEach-Object { Write-Host $_ }
        $errors.Add("Unit tests failed")
    } elseif ($analysisSkips.Count -ne 0) {
        $analysisSkips | ForEach-Object { Write-Host $_ }
        $errors.Add("Analytical tests were skipped after setup")
    } else {
        Write-Host "PASS Full unit tests (no analytical skips)"
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
Write-Host "Python environment: $venvPython"
Write-Host "One-time Chrome setup still requires the user:"
Write-Host "1. Load unpacked extension from: $extensionPath"
Write-Host "2. Allow trends.google.co.th and set Chrome Downloads to: $incomingPath"
Write-Host "3. Turn off 'Ask where to save each file'"
Write-Host ""
Write-Host "Monthly prepare: .\scripts\toolkit.ps1 monthly-prepare"
Write-Host "Monthly finish:  .\scripts\toolkit.ps1 monthly-finish"
Write-Host "Queue file: $jobsPath"
