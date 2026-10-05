[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [ValidateRange(1, 100000)] [int]$Episodes = 100
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Assert-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE). Stop here and preserve the output." }
}

try {
    $VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        if (-not $PythonPath) {
            if (Get-Command py -ErrorAction SilentlyContinue) {
                foreach ($version in @("3.11", "3.12", "3.13")) {
                    try {
                        $candidate = & py "-$version" -c "import sys; print(sys.executable)" 2>$null
                        if ($LASTEXITCODE -eq 0 -and $candidate) {
                            $PythonPath = [string]($candidate | Select-Object -Last 1)
                            break
                        }
                    } catch { }
                }
            }
            if (-not $PythonPath -and (Get-Command python -ErrorAction SilentlyContinue)) {
                $candidate = & python -c "import sys; print(sys.executable)"
                if ($LASTEXITCODE -eq 0) { $PythonPath = [string]($candidate | Select-Object -Last 1) }
            }
        }
        if (-not $PythonPath) { throw "No Python found. Run 'py -0p' or pass -PythonPath with an existing Python 3.11/3.12/3.13 executable." }
        & $PythonPath -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,14), 'Use Python 3.11, 3.12 or 3.13'; print(sys.version)"
        Assert-Exit "Python version check"
        & $PythonPath -m venv .venv
        Assert-Exit "Virtual environment creation"
    }
    & $VenvPython -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,14), 'Existing .venv needs Python 3.11/3.12/3.13'"
    Assert-Exit "Virtual environment check"
    & $VenvPython -m pip install -e ".[sim]"
    Assert-Exit "Dependency installation"
    & $VenvPython -m unittest discover -s tests -v
    Assert-Exit "Unit and integration tests"

    $Tag = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $Doctor = "runs\doctor_$Tag.json"
    & $VenvPython -m cornercaselab doctor --smoke --out $Doctor
    Assert-Exit "Simulator smoke check"
    $Run = "runs\pilot_$Tag"
    & $VenvPython -m cornercaselab run --episodes $Episodes --seed 20261003 --out $Run
    Assert-Exit "Random pilot"
    $freeze = & $VenvPython -m pip freeze
    Assert-Exit "Environment version snapshot"
    $freeze | Set-Content -Encoding UTF8 (Join-Path $Run "environment-freeze.txt")
    Write-Host ""
    Write-Host "Pilot completed. This is NOT a new-method comparison or real-road safety evidence."
    Write-Host "Summary: $(Join-Path $PSScriptRoot "$Run\summary.json")"
    Write-Host "Environment check: $(Join-Path $PSScriptRoot $Doctor)"
} catch {
    Write-Host ""
    Write-Host "STOPPED: $($_.Exception.Message)"
    Write-Host "Copy the error and the last completed step. No remote repository was created."
    exit 1
}
