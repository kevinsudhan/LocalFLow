<#
.SYNOPSIS
    Prepare a development machine for LocalFlow.

.DESCRIPTION
    Creates the Python virtual environment, installs backend dependencies
    (including the CUDA runtime wheels when an NVIDIA GPU is present),
    downloads the Silero VAD model, installs the frontend packages and
    generates the application icons.

    Safe to re-run: every step is idempotent.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
    powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -SkipGpu
#>
param(
    [switch]$SkipGpu,
    [switch]$SkipNode,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($message) { Write-Host "`n=== $message ===" -ForegroundColor Cyan }
function Ok($message) { Write-Host "  OK  $message" -ForegroundColor Green }
function Warn($message) { Write-Host "  !!  $message" -ForegroundColor Yellow }

# ---------------------------------------------------------------- Python ---
Step "Python environment"

if ($PythonExe -eq "") {
    # Prefer 3.11: it is what CTranslate2 and onnxruntime publish wheels for.
    foreach ($candidate in @("py -3.11", "py -3.12", "python")) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $version = & $exe $parts[1..($parts.Length - 1)] --version 2>&1
            if ($version -match "Python 3\.(1[0-3])") {
                $PythonExe = $candidate
                break
            }
        }
    }
}
if ($PythonExe -eq "") {
    throw "Python 3.10-3.13 was not found. Install it from python.org and re-run this script."
}
Write-Host "  Using: $PythonExe"

$venv = Join-Path $Root ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    $parts = $PythonExe.Split(" ")
    & $parts[0] $parts[1..($parts.Length - 1)] -m venv $venv
    Ok "Created .venv"
} else {
    Ok ".venv already exists"
}

$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $Root "backend\requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
Ok "Backend dependencies installed"

# ------------------------------------------------------------------- GPU ---
Step "GPU support"

$hasNvidia = $false
try {
    $smi = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null
    if ($LASTEXITCODE -eq 0 -and $smi) {
        $hasNvidia = $true
        Write-Host "  Detected: $smi"
    }
} catch { }

if ($SkipGpu) {
    Warn "Skipped on request. LocalFlow will use the CPU."
} elseif (-not $hasNvidia) {
    Warn "No NVIDIA GPU found. LocalFlow will use the CPU, which works but is slower."
} else {
    # CTranslate2 needs cuBLAS and cuDNN 9. The pip wheels avoid a multi-gigabyte
    # CUDA Toolkit install; LocalFlow registers their DLL directories at startup.
    & $venvPython -m pip install -r (Join-Path $Root "backend\requirements-gpu.txt")
    if ($LASTEXITCODE -ne 0) {
        Warn "CUDA wheels failed to install. LocalFlow will fall back to the CPU."
    } else {
        Ok "CUDA runtime libraries installed"
    }
}

$probe = & $venvPython -c @"
import sys
sys.path.insert(0, 'backend')
from localflow.asr.gpu import probe
info = probe()
print('cuda' if info['cuda_available'] else 'cpu', info.get('gpu_name',''), info.get('vram_total_mb',0))
"@ 2>&1
Write-Host "  Runtime check: $probe"

# ------------------------------------------------------------------- VAD ---
Step "Voice activity detection model"
& $venvPython (Join-Path $Root "scripts\download_vad.py")
if ($LASTEXITCODE -ne 0) {
    Warn "Could not download Silero VAD. LocalFlow will use its energy-based fallback."
} else {
    Ok "Silero VAD ready"
}

# ------------------------------------------------------------------ Node ---
if (-not $SkipNode) {
    Step "Frontend"
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Warn "npm was not found. Install Node.js 18+ to build the desktop shell."
    } else {
        Push-Location (Join-Path $Root "desktop")
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm install failed." }
        Pop-Location
        Ok "Frontend packages installed"
    }
}

# ----------------------------------------------------------------- Icons ---
Step "Icons"
& $venvPython (Join-Path $Root "scripts\make_icons.py")
Ok "Icons generated"

# ------------------------------------------------------------ Toolchains ---
Step "Build toolchain"
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    Ok "Rust: $(cargo --version)"
} else {
    Warn "Rust was not found. Install it from https://rustup.rs to build the desktop app."
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $vc = & $vswhere -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($vc) { Ok "MSVC build tools found" }
    else { Warn "MSVC C++ build tools are missing. Install the 'Desktop development with C++' workload." }
} else {
    Warn "Visual Studio Build Tools were not found. Tauri needs the MSVC linker."
}

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Ok "Ollama: $(ollama --version)"
} else {
    Warn "Ollama was not found. AI cleanup will be unavailable until you install it from ollama.com (everything else still works)."
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "  Run the app:   powershell -File scripts/build.ps1 -Dev"
Write-Host "  Run the tests: .\.venv\Scripts\python.exe -m pytest"
