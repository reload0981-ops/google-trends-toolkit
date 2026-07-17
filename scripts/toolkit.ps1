[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("setup", "monthly-prepare", "monthly-finish")]
    [string]$Action,

    [string]$Python = "",
    [string]$RequireLatest = "",
    [Alias("out")]
    [string]$JobOutput = "",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
Set-Location -LiteralPath $root

function Invoke-Native(
    [string]$Executable,
    [string[]]$ArgumentList,
    [string]$Label
) {
    Write-Host "==> $Label"
    & $Executable @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Assert-VenvPython {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Analysis environment not found. Run '.\scripts\toolkit.ps1 setup' first."
    }
}

switch ($Action) {
    "setup" {
        if ($Arguments.Count -ne 0 -or $RequireLatest -or $JobOutput) {
            throw "setup accepts only the optional -Python parameter"
        }
        $bootstrap = Join-Path $root "bootstrap-windows.ps1"
        if ($Python) {
            & $bootstrap -Python $Python
        } else {
            & $bootstrap
        }
        if (-not $?) {
            throw "Machine bootstrap failed"
        }
    }

    "monthly-prepare" {
        if ($RequireLatest) {
            throw "-RequireLatest is valid only with monthly-finish"
        }
        Assert-VenvPython
        $jobArguments = @($Arguments)
        if ($jobArguments.Count -eq 0) {
            $jobArguments = @("--all")
        }
        if ($JobOutput) {
            $jobArguments += @("--out", $JobOutput)
        }
        Invoke-Native $venvPython (@("-X", "utf8", "collector/make_jobs.py") + $jobArguments) "Create extension queue"
        $queuePath = "extension\data\jobs.json"
        if ($JobOutput) {
            $queuePath = $JobOutput
        }
        Write-Host ""
        Write-Host "QUEUE READY: $queuePath" -ForegroundColor Green
        Write-Host "In Chrome Controller: Import jobs.json, press Start, and resolve CAPTCHA if prompted."
    }

    "monthly-finish" {
        if ($Arguments.Count -ne 0 -or $JobOutput) {
            throw "monthly-finish accepts only the optional -RequireLatest YYYY-MM parameter"
        }
        Assert-VenvPython

        Invoke-Native $venvPython @("-X", "utf8", "collector/ingest.py", "--dry-run") "Validate incoming files (dry run)"
        Invoke-Native $venvPython @("-X", "utf8", "collector/ingest.py") "Ingest incoming files"
        Invoke-Native $venvPython @("-X", "utf8", "collector/audit.py", "--strict") "Audit raw dataset structure"

        $freshnessArguments = @("-X", "utf8", "collector/audit.py", "--strict", "--require-latest")
        if ($RequireLatest) {
            $freshnessArguments += $RequireLatest
        }
        Invoke-Native $venvPython $freshnessArguments "Audit raw dataset freshness"
        Invoke-Native $venvPython @("-X", "utf8", "collector/build_site_data.py", "--check") "Verify generated site data"
        Invoke-Native $venvPython @("-X", "utf8", "-m", "analysis.build") "Build analytical outputs"
        Invoke-Native $venvPython @("-X", "utf8", "-m", "analysis.build", "--check") "Byte-check analytical outputs"
        Invoke-Native $venvPython @("-X", "utf8", "-m", "analysis.build", "--audit") "Audit analytical outputs"
        Invoke-Native $venvPython @("-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-v") "Run full test suite"

        $git = Get-Command "git" -ErrorAction Stop
        Invoke-Native $git.Source @("status", "--short") "Show release working tree"
        Write-Host ""
        Write-Host "MONTHLY CHECKS PASSED" -ForegroundColor Green
        Write-Host "Nothing was staged, committed, pushed, or deployed. Review git status before publishing."
    }
}
