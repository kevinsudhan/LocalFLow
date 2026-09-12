"""Build a relocatable Python runtime for the installer.

A virtualenv is not relocatable: `pyvenv.cfg` points at the interpreter it was
created from, so copying `.venv` into an installer produces something that only
works on the build machine. The embeddable distribution *is* relocatable, so we
download it and graft the installed packages onto it.

    python scripts/bundle_runtime.py              # includes CUDA (~2.2 GB)
    python scripts/bundle_runtime.py --skip-gpu   # CPU only (~500 MB)
"""
from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
TARGET = ROOT / "desktop" / "src-tauri" / "python-runtime"
EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"

# Directories that exist only to support development or are re-created at runtime.
PRUNE_DIRS = {"__pycache__", "tests", "test", "testing", ".pytest_cache", "pip", "setuptools"}
PRUNE_SUFFIXES = {".pyc", ".pyo", ".pyi", ".c", ".h", ".cpp", ".pdb"}
# Packages needed only by the test suite and the benchmarks.
PRUNE_PACKAGES = {
    "pytest", "_pytest", "pluggy", "iniconfig", "coverage", "pytest_cov",
    "pytest_asyncio", "py", "attr",
}
GPU_PACKAGES = {"nvidia"}


def venv_python_version() -> str:
    """The exact x.y.z of the interpreter in .venv."""
    import subprocess

    exe = VENV / "Scripts" / "python.exe"
    if not exe.is_file():
        raise SystemExit("No .venv found. Run scripts/setup.ps1 first.")
    out = subprocess.run(
        [str(exe), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def download_embeddable(version: str, destination: Path) -> Path:
    archive = destination / f"python-{version}-embed-amd64.zip"
    if archive.is_file() and archive.stat().st_size > 5_000_000:
        print(f"  using cached {archive.name}")
        return archive
    url = EMBED_URL.format(v=version)
    print(f"  downloading {url}")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=180) as response, archive.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except Exception as exc:
        raise SystemExit(
            f"Could not download the embeddable Python for {version}: {exc}\n"
            "Check your connection, or build without -Installer."
        ) from exc
    return archive


def enable_site_packages(runtime: Path, version: str) -> None:
    """The embeddable build disables `site` by default; the bundle needs it."""
    tag = "".join(version.split(".")[:2])
    pth = runtime / f"python{tag}._pth"
    if not pth.is_file():
        candidates = list(runtime.glob("python*._pth"))
        if not candidates:
            raise SystemExit("The embeddable archive had no ._pth file.")
        pth = candidates[0]
    lines = pth.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        out.append("import site" if line.strip() == "#import site" else line)
    if "Lib\\site-packages" not in out:
        out.insert(max(0, len(out) - 1), "Lib\\site-packages")
    if "import site" not in out:
        out.append("import site")
    pth.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  enabled site-packages in {pth.name}")


def should_skip(path: Path, relative: Path, skip_gpu: bool) -> bool:
    top = relative.parts[0] if relative.parts else ""
    package = top.split("-")[0].split(".")[0]
    if package in PRUNE_PACKAGES:
        return True
    if skip_gpu and package in GPU_PACKAGES:
        return True
    if any(part in PRUNE_DIRS for part in relative.parts):
        return True
    if path.suffix in PRUNE_SUFFIXES:
        return True
    return False


def copy_packages(runtime: Path, skip_gpu: bool) -> int:
    source = VENV / "Lib" / "site-packages"
    destination = runtime / "Lib" / "site-packages"
    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    total_bytes = 0
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if should_skip(path, relative, skip_gpu):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
        total_bytes += path.stat().st_size
    print(f"  copied {copied} files ({total_bytes / 1e6:.0f} MB)")
    return total_bytes


def copy_backend(runtime: Path) -> None:
    """Ship the backend source next to the interpreter."""
    source = ROOT / "backend" / "localflow"
    destination = runtime / "Lib" / "site-packages" / "localflow"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    print("  copied the LocalFlow backend")


def verify(runtime: Path) -> None:
    import subprocess

    exe = runtime / "python.exe"
    probe = (
        "import localflow, faster_whisper, sounddevice, onnxruntime, websockets;"
        "print('runtime ok', localflow.__version__, faster_whisper.__version__)"
    )
    result = subprocess.run([str(exe), "-c", probe], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            "The bundled runtime could not import LocalFlow:\n" + result.stderr[-1500:]
        )
    print("  " + result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-gpu",
        action="store_true",
        help="exclude the CUDA wheels; the installed app will use the CPU",
    )
    parser.add_argument("--force", action="store_true", help="rebuild from scratch")
    args = parser.parse_args()

    version = venv_python_version()
    print(f"Bundling a standalone Python {version} runtime")

    if TARGET.exists():
        if not args.force:
            print(f"  {TARGET} already exists; use --force to rebuild")
            verify(TARGET)
            return 0
        shutil.rmtree(TARGET)

    cache = ROOT / ".cache"
    archive = download_embeddable(version, cache)

    TARGET.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(TARGET)
    print(f"  extracted to {TARGET}")

    enable_site_packages(TARGET, version)
    total = copy_packages(TARGET, args.skip_gpu)
    copy_backend(TARGET)
    verify(TARGET)

    size = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file())
    print(f"\nRuntime ready: {size / 1e6:.0f} MB at {TARGET}")
    if args.skip_gpu:
        print("  GPU libraries excluded - the installed app will transcribe on the CPU.")
    elif total > 1_500_000_000:
        print("  Includes CUDA libraries. Use --skip-gpu for a much smaller installer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
