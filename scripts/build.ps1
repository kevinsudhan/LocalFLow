<#
.SYNOPSIS
    Build, run or package LocalFlow.

.DESCRIPTION
    -Dev       Run the app with hot reload (Vite + Tauri dev).
    -Build     Produce a release binary.
    -Installer Produce the NSIS installer, bundling a standalone Python runtime.
    -Test      Run the Rust, Python and frontend test suites.
    -Bench     Run the dictation quality benchmark.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Dev
    powershell -ExecutionPolicy Bypass -File scripts/build.ps1 -Installer
#>
param(
    [switch]$Dev,
    [switch]$Build,
    [switch]$Installer,
    [switch]$Test,
    [switch]$Bench,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Desktop = Join-Path $Root "desktop"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

function Step($message) { Write-Host "`n=== $message ===" -ForegroundColor Cyan }
function Fail($message) { Write-Host "  $message" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $VenvPython)) {
    Fail "No Python environment. Run scripts/setup.ps1 first."
}

if (-not ($Dev -or $Build -or $Installer -or $Test -or $Bench -or $Clean)) {
    $Dev = $true
}

# ----------------------------------------------------------------- clean ---
if ($Clean) {
    Step "Cleaning build output"
    Remove-Item -Recurse -Force (Join-Path $Desktop "dist") -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $Desktop "src-tauri\target\release\bundle") -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $Desktop "src-tauri\python-runtime") -ErrorAction SilentlyContinue
    Write-Host "  Done."
}

# ------------------------------------------------------------------ test ---
if ($Test) {
    Step "Python tests"
    Push-Location $Root
    $env:PYTHONPATH = "backend"
    & $VenvPython -m pytest -q
    $pythonOk = $LASTEXITCODE -eq 0
    Pop-Location

    Step "Rust tests"
    Push-Location (Join-Path $Desktop "src-tauri")
    cargo test --quiet
    $rustOk = $LASTEXITCODE -eq 0
    Pop-Location

    Step "Frontend typecheck"
    Push-Location $Desktop
    npm run typecheck
    $tsOk = $LASTEXITCODE -eq 0
    Pop-Location

    Write-Host ""
    Write-Host ("  Python:   " + $(if ($pythonOk) { "pass" } else { "FAIL" }))
    Write-Host ("  Rust:     " + $(if ($rustOk) { "pass" } else { "FAIL" }))
    Write-Host ("  Frontend: " + $(if ($tsOk) { "pass" } else { "FAIL" }))
    if (-not ($pythonOk -and $rustOk -and $tsOk)) { exit 1 }
}

# ----------------------------------------------------------------- bench ---
if ($Bench) {
    Step "Dictation quality benchmark"
    Push-Location $Root
    $env:PYTHONPATH = "backend"
    & $VenvPython benchmarks/run_benchmark.py --json benchmarks/results.json
    Pop-Location
}

# ------------------------------------------------------------------- dev ---
if ($Dev) {
    Step "Starting LocalFlow (development)"
    Write-Host "  The backend runs from .venv; the frontend hot-reloads."
    Write-Host "  Close the window or press Ctrl+C to stop.`n"
    Push-Location $Desktop
    $env:LOCALFLOW_PYTHON = $VenvPython
    $env:LOCALFLOW_BACKEND_DIR = Join-Path $Root "backend"
    npm run tauri:dev
    Pop-Location
}

# ----------------------------------------------------------------- build ---
if ($Build -or $Installer) {
    Step "Building the frontend"
    Push-Location $Desktop
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Frontend build failed." }
    Pop-Location

    if ($Installer) {
        Step "Preparing the bundled Python runtime"
        & $VenvPython (Join-Path $Root "scripts\bundle_runtime.py")
        if ($LASTEXITCODE -ne 0) {
            Fail "Could not build the standalone Python runtime."
        }
    }

    Step $(if ($Installer) { "Building the installer" } else { "Building the release binary" })
    Push-Location $Desktop
    if ($Installer) {
        npm run tauri:build
    } else {
        npm run tauri -- build --no-bundle
    }
    $ok = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $ok) { Fail "Tauri build failed." }

    $exe = Join-Path $Desktop "src-tauri\target\release\localflow.exe"
    if (Test-Path $exe) {
        $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
        Write-Host "`n  Executable: $exe ($size MB)" -ForegroundColor Green
    }
    if ($Installer) {
        $bundle = Join-Path $Desktop "src-tauri\target\release\bundle\nsis"
        Get-ChildItem $bundle -Filter *.exe -ErrorAction SilentlyContinue | ForEach-Object {
            $size = [math]::Round($_.Length / 1MB, 1)
            Write-Host "  Installer:  $($_.FullName) ($size MB)" -ForegroundColor Green
        }
    }
}
