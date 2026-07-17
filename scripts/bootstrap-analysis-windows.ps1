[CmdletBinding()]
param(
    [string]$Python = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$requirements = Join-Path $root "requirements-analysis.txt"
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

$x13Version = "1.1-b62"
$x13Url = "https://www2.census.gov/software/x-13arima-seats/x13as/windows/program-archives/x13as_ascii-v1-1-b62.zip"
$x13ZipSha256 = "C6BD65132A3219555D00ABF794649E75C133F23C0DB066ED03C0B5CA30E694C2"
$x13ExeSha256 = "2E43194361EE096797F0431765193C316196EA6776F11535E76281A413D49669"
$x13Root = Join-Path $root ".tools\x13"
$x13Dir = Join-Path $x13Root $x13Version
$x13Exe = Join-Path $x13Dir "x13as.exe"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-Sha256([string]$Path, [string]$Expected) {
    $actual = Get-Sha256 $Path
    if ($actual -ne $Expected) {
        throw "SHA-256 mismatch for ${Path}: expected $Expected, got $actual"
    }
}

function Find-Python([string]$Requested) {
    if ($Requested) {
        $command = Get-Command $Requested -ErrorAction SilentlyContinue
        if (-not $command) {
            throw "Python command not found: $Requested"
        }
        return $command.Source
    }
    foreach ($name in @("python", "py")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }
        try {
            $candidateVersion = Get-PythonVersion $command.Source
            if ($candidateVersion -ge [version]"3.11" -and $candidateVersion -lt [version]"3.14") {
                return $command.Source
            }
        } catch {
            # Windows Store aliases can exist in PATH without launching Python.
            continue
        }
    }
    throw "Python 3.11-3.13 not found. Install Python, then rerun with -Python <path>."
}

function Get-PythonVersion([string]$Executable) {
    $versionText = & $Executable -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not run Python: $Executable"
    }
    return [version]$versionText.Trim()
}

function Assert-SupportedPython([version]$Version, [string]$Label) {
    if ($Version -lt [version]"3.11" -or $Version -ge [version]"3.14") {
        throw "$Label must be Python 3.11, 3.12, or 3.13; found $Version"
    }
}

function Add-X13Candidate(
    [System.Collections.Generic.List[string]]$Candidates,
    [string]$Value
) {
    if (-not $Value) {
        return
    }
    if (Test-Path -LiteralPath $Value -PathType Leaf) {
        [void]$Candidates.Add($Value)
        return
    }
    foreach ($name in @("x13as.exe", "x13as_ascii.exe")) {
        $candidate = Join-Path $Value $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            [void]$Candidates.Add($candidate)
        }
    }
}

Write-Host "Analysis environment bootstrap"
Write-Host "Repo: $root"

$basePython = Find-Python $Python
$baseVersion = Get-PythonVersion $basePython
Assert-SupportedPython $baseVersion "Base Python"
Write-Host "PASS Base Python $baseVersion"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    if (Test-Path -LiteralPath $venv) {
        throw "Existing .venv is incomplete: $venv. Remove or repair it, then rerun."
    }
    & $basePython -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment: $venv"
    }
    Write-Host "Created $venv"
}

$venvVersion = Get-PythonVersion $venvPython
Assert-SupportedPython $venvVersion "Virtual environment"
& $venvPython -m pip install --disable-pip-version-check --requirement $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install analysis dependencies"
}
& $venvPython -X utf8 -c "import numpy, pandas, scipy, statsmodels; print(f'PASS Analysis dependencies: numpy={numpy.__version__}, pandas={pandas.__version__}, scipy={scipy.__version__}, statsmodels={statsmodels.__version__}')"
if ($LASTEXITCODE -ne 0) {
    throw "Analysis dependency import check failed"
}

$x13Ready = $false
if (Test-Path -LiteralPath $x13Exe -PathType Leaf) {
    if ((Get-Sha256 $x13Exe) -eq $x13ExeSha256) {
        $x13Ready = $true
        Write-Host "PASS Existing repo-local X-13 hash"
    } else {
        Write-Host "Repo-local X-13 has the wrong hash; replacing it"
    }
}

if (-not $x13Ready) {
    $candidates = [System.Collections.Generic.List[string]]::new()
    Add-X13Candidate $candidates $env:X13PATH
    Add-X13Candidate $candidates (Join-Path $env:USERPROFILE "x13as")
    $pathCommand = Get-Command "x13as.exe" -ErrorAction SilentlyContinue
    if ($pathCommand) {
        Add-X13Candidate $candidates $pathCommand.Source
    }

    $existingX13 = $null
    foreach ($candidate in $candidates) {
        if ((Get-Sha256 $candidate) -eq $x13ExeSha256) {
            $existingX13 = $candidate
            break
        }
    }

    New-Item -ItemType Directory -Path $x13Dir -Force | Out-Null
    if ($existingX13) {
        Copy-Item -LiteralPath $existingX13 -Destination $x13Exe -Force
        Write-Host "Reused verified local X-13: $existingX13"
    } else {
        $staging = Join-Path $x13Root (".staging-" + [guid]::NewGuid().ToString("N"))
        $zipPath = Join-Path $staging "x13as.zip"
        $extractPath = Join-Path $staging "extract"
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Write-Host "Downloading official X-13 $x13Version from the U.S. Census Bureau..."
            Invoke-WebRequest -UseBasicParsing -Uri $x13Url -OutFile $zipPath
            Assert-Sha256 $zipPath $x13ZipSha256
            Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force

            $archiveDir = Join-Path $extractPath "x13as"
            $archiveExe = Join-Path $archiveDir "x13as_ascii.exe"
            Assert-Sha256 $archiveExe $x13ExeSha256
            Copy-Item -Path (Join-Path $archiveDir "*") -Destination $x13Dir -Recurse -Force
            Copy-Item -LiteralPath $archiveExe -Destination $x13Exe -Force
        } finally {
            if (Test-Path -LiteralPath $staging) {
                Remove-Item -LiteralPath $staging -Recurse -Force
            }
        }
    }
}

Assert-Sha256 $x13Exe $x13ExeSha256
$versionOutput = (& $x13Exe -v 2>&1 | Out-String)
if ($versionOutput -notmatch "Version Number\s+1\.1\s+Build\s+62") {
    throw "X-13 version check failed: $x13Exe"
}
$env:X13PATH = $x13Dir
Write-Host "PASS X-13ARIMA-SEATS 1.1 Build 62: $x13Exe"

& $venvPython -X utf8 -c "from statsmodels.tsa.x13 import _find_x12; p=_find_x12(prefer_x13=True); assert p, 'X-13 not detected'; print('PASS statsmodels X-13 discovery: ' + p)"
if ($LASTEXITCODE -ne 0) {
    throw "statsmodels could not discover X-13"
}

Write-Host ""
Write-Host "ANALYSIS MACHINE READY" -ForegroundColor Green
Write-Host "Python: $venvPython"
Write-Host "X13PATH for this process: $x13Dir"
