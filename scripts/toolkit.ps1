[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("setup", "monthly-prepare", "monthly-finish", "monthly-run", "add-keyword")]
    [string]$Action,

    [string]$Python = "",
    [string]$RequireLatest = "",
    [Alias("out")]
    [string]$JobOutput = "",

    [switch]$DesktopCopy,

    [switch]$AllowUnfinishedRound,

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
    # 9 means a guard stopped on purpose and already explained itself in Thai.
    # Re-raising it would bury that explanation under a PowerShell stack trace.
    if ($LASTEXITCODE -eq 9) { exit 9 }
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
    [string]$OutputPath = "",
    [string]$DesktopKind = "",
    [switch]$AllowUnfinishedRound
) {
    Assert-VenvPython
    if ($OutputPath -and $DesktopKind) {
        throw "-DesktopCopy cannot be combined with -out; the desktop copy is made from the canonical queue"
    }
    $jobArguments = @($QueueArguments)
    if ($jobArguments.Count -eq 0) {
        $jobArguments = @("--all")
    }
    if ($OutputPath) {
        $jobArguments += @("--out", $OutputPath)
    }
    # Python owns the desktop copy so the Thai filenames stay out of this file,
    # which Windows PowerShell 5.1 would read as ANSI without a BOM.
    if ($AllowUnfinishedRound) {
        $jobArguments += @("--allow-unfinished-round")
    }
    if ($DesktopKind) {
        $jobArguments += @("--desktop-dir", [Environment]::GetFolderPath("Desktop"), "--desktop-kind", $DesktopKind)
    }
    Invoke-Native $venvPython (@("-X", "utf8", "collector/make_jobs.py") + $jobArguments) "Create extension queue"

    Write-Host ""
    Write-Host "QUEUE READY" -ForegroundColor Green
    Write-Host "In Chrome Controller: Import jobs.json, choose the file shown above, press Start."
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
        if ($Arguments.Count -ne 0 -or $RequireLatest -or $JobOutput -or $DesktopCopy -or $AllowUnfinishedRound) {
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
        $kind = ""
        if ($DesktopCopy) { $kind = "monthly" }
        Invoke-MonthlyPrepare -QueueArguments $Arguments -OutputPath $JobOutput -DesktopKind $kind -AllowUnfinishedRound:$AllowUnfinishedRound
    }

    "monthly-finish" {
        if ($Arguments.Count -ne 0 -or $JobOutput -or $DesktopCopy) {
            throw "monthly-finish accepts only the optional -RequireLatest YYYY-MM parameter"
        }
        Invoke-MonthlyFinish -LatestMonth $RequireLatest
    }

    "add-keyword" {
        if ($Arguments.Count -ne 0 -or $RequireLatest -or $JobOutput) {
            throw "add-keyword takes no extra parameters"
        }
        Assert-VenvPython
        # add_keyword.py runs the same guard before it writes the row.

        # Python owns every Thai prompt: Windows PowerShell 5.1 reads a script
        # without a BOM as ANSI, so Thai text in this file would be mojibake.
        # It hands the allocated id back through a file because capturing stdout
        # here would hide the prompts from the person answering them.
        $idFile = Join-Path ([IO.Path]::GetTempPath()) ("gt-new-keyword-" + [Guid]::NewGuid().ToString("N") + ".txt")
        try {
            Invoke-Native $venvPython @("-X", "utf8", "collector/add_keyword.py", "--interactive", "--id-file", $idFile) "Add the keyword"
            if (-not (Test-Path -LiteralPath $idFile)) {
                throw "The keyword was not added"
            }
            $keywordId = (Get-Content -LiteralPath $idFile -Raw).Trim()
        } finally {
            if (Test-Path -LiteralPath $idFile) { Remove-Item -LiteralPath $idFile -Force }
        }

        Invoke-MonthlyPrepare -QueueArguments @("--ids", $keywordId) -DesktopKind "keyword"

        Write-Host ""
        Write-Host "CHROME CHECKPOINT" -ForegroundColor Yellow
        Write-Host "1. Import the queue shown above and press Start."
        Write-Host "2. Resolve CAPTCHA if prompted."
        Write-Host "3. Continue only when all 6 areas are collected and FAILED is 0."
        $confirmation = Read-Host "Type FINISH to screen this keyword and set its tier"
        if ($confirmation -cne "FINISH") {
            Write-Host "STOPPED before screening. The row stays in keywords.csv." -ForegroundColor Yellow
            Write-Host "Remove it with: collector\add_keyword.py --remove $keywordId"
            break
        }

        Invoke-Native $venvPython @("-X", "utf8", "collector/add_keyword.py", "--finalize", $keywordId) "Screen the keyword and set its tier"
        Invoke-MonthlyFinish
        Write-Host "NEW KEYWORD READY" -ForegroundColor Green
        Write-Host "Publish with keywords.csv included in the staged allowlist."
    }

    "monthly-run" {
        $kind = ""
        if ($DesktopCopy) { $kind = "monthly" }
        Invoke-MonthlyPrepare -QueueArguments $Arguments -OutputPath $JobOutput -DesktopKind $kind -AllowUnfinishedRound:$AllowUnfinishedRound
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
