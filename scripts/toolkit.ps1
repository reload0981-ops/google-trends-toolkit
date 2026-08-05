[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("setup", "monthly-prepare", "monthly-finish", "monthly-run")]
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

function Invoke-MonthlyPrepare(
    [string[]]$QueueArguments = @(),
    [string]$OutputPath = ""
) {
    Assert-VenvPython
    $jobArguments = @($QueueArguments)
    if ($jobArguments.Count -eq 0) {
        $jobArguments = @("--all")
    }
    if ($OutputPath) {
        $jobArguments += @("--out", $OutputPath)
    }
    Invoke-Native $venvPython (@("-X", "utf8", "collector/make_jobs.py") + $jobArguments) "Create extension queue"
    $queuePath = "extension\data\jobs.json"
    if ($OutputPath) {
        $queuePath = $OutputPath
    }
    Write-Host ""
    Write-Host "QUEUE READY: $queuePath" -ForegroundColor Green
    Write-Host "In Chrome Controller: Import jobs.json, press Start, and resolve CAPTCHA if prompted."
}

function Invoke-MonthlyFinish(
    [string]$LatestMonth = ""
) {
    Assert-VenvPython
    Invoke-Native $venvPython @("-X", "utf8", "collector/ingest.py", "--dry-run") "Validate incoming files (dry run)"
    Invoke-Native $venvPython @("-X", "utf8", "collector/ingest.py") "Ingest incoming files"
    Invoke-Native $venvPython @("-X", "utf8", "collector/audit.py", "--strict") "Audit raw dataset structure"

    $freshnessArguments = @("-X", "utf8", "collector/audit.py", "--strict", "--require-latest")
    if ($LatestMonth) {
        $freshnessArguments += $LatestMonth
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
    Write-Host "Tableau source: derived\sa_pipeline_v3\series.csv"
    Write-Host "Nothing was staged, committed, pushed, or deployed. Review git status before publishing."
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
            throw "-RequireLatest is valid only with monthly-run or monthly-finish"
        }
        Invoke-MonthlyPrepare -QueueArguments $Arguments -OutputPath $JobOutput
    }

    "monthly-finish" {
        if ($Arguments.Count -ne 0 -or $JobOutput) {
            throw "monthly-finish accepts only the optional -RequireLatest YYYY-MM parameter"
        }
        Invoke-MonthlyFinish -LatestMonth $RequireLatest
    }

    "monthly-run" {
        Invoke-MonthlyPrepare -QueueArguments $Arguments -OutputPath $JobOutput
        Write-Host ""
        Write-Host "CHROME CHECKPOINT" -ForegroundColor Yellow
        Write-Host "1. Import the queue shown above and press Start."
        Write-Host "2. Resolve CAPTCHA if prompted."
        Write-Host "3. Continue only when the Controller has 0 FAILED jobs and the CSV files are in incoming\."
        $confirmation = Read-Host "Type FINISH to ingest, validate, and build the Tableau output"
        if ($confirmation -cne "FINISH") {
            Write-Host "MONTHLY LOOP STOPPED before ingest. Re-run monthly-run when ready." -ForegroundColor Yellow
            break
        }
        Invoke-MonthlyFinish -LatestMonth $RequireLatest
        Write-Host "MONTHLY LOOP COMPLETE" -ForegroundColor Green
    }
}
