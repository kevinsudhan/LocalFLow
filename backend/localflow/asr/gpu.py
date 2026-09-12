"""GPU detection and CUDA DLL wiring for CTranslate2.

CTranslate2 needs cuBLAS and cuDNN 9 at load time.  We ship them as pip wheels
(``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12``) rather than asking the user to
install a multi-gigabyte CUDA Toolkit, which means the DLL directories have to
be registered with the loader *before* the first CUDA call.  Call
:func:`prepare_cuda` once at process start.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_prepared = False
_dll_dirs: list[str] = []


def _nvidia_dll_dirs() -> list[Path]:
    """Locate the ``bin`` folders inside the installed nvidia-* wheels."""
    dirs: list[Path] = []
    for base in sys.path:
        nvidia = Path(base) / "nvidia"
        if not nvidia.is_dir():
            continue
        for sub in nvidia.iterdir():
            for leaf in ("bin", "lib"):
                candidate = sub / leaf
                if candidate.is_dir() and any(candidate.glob("*.dll")):
                    dirs.append(candidate)
        if dirs:
            break
    return dirs


def prepare_cuda() -> list[str]:
    """Register bundled CUDA DLL directories.  Safe to call repeatedly."""
    global _prepared, _dll_dirs
    if _prepared:
        return _dll_dirs
    _prepared = True
    if os.name != "nt":
        return _dll_dirs
    found = _nvidia_dll_dirs()
    for path in found:
        try:
            os.add_dll_directory(str(path))
            _dll_dirs.append(str(path))
        except (OSError, AttributeError):
            log.debug("Could not register DLL dir %s", path, exc_info=True)
    if _dll_dirs:
        os.environ["PATH"] = os.pathsep.join(_dll_dirs + [os.environ.get("PATH", "")])
        log.info("Registered %d CUDA DLL directories", len(_dll_dirs))
    else:
        log.info("No bundled CUDA libraries found; GPU inference may be unavailable")
    return _dll_dirs


@lru_cache(maxsize=1)
def nvidia_smi() -> dict[str, Any] | None:
    """Query the driver directly; returns None when there is no NVIDIA GPU."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        for guess in (
            r"C:\Windows\System32\nvidia-smi.exe",
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ):
            if Path(guess).is_file():
                exe = guess
                break
    if not exe:
        return None
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.total,memory.used,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    first = out.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) < 5:
        return None
    try:
        return {
            "name": parts[0],
            "vram_total_mb": int(float(parts[1])),
            "vram_used_mb": int(float(parts[2])),
            "vram_free_mb": int(float(parts[3])),
            "driver": parts[4],
        }
    except ValueError:
        return None


def cuda_device_count() -> int:
    prepare_cuda()
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def supported_compute_types(device: str) -> set[str]:
    prepare_cuda()
    try:
        import ctranslate2

        return set(ctranslate2.get_supported_compute_types(device))
    except Exception:
        return set()


def system_memory_mb() -> dict[str, int]:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {"total": vm.total // (1024 * 1024), "available": vm.available // (1024 * 1024)}
    except Exception:
        return {"total": 0, "available": 0}


def probe() -> dict[str, Any]:
    """Everything the Model Manager and diagnostics panel need to show."""
    prepare_cuda()
    smi = nvidia_smi()
    count = cuda_device_count()
    cuda_ok = count > 0
    return {
        "cuda_available": cuda_ok,
        "cuda_device_count": count,
        "gpu": smi,
        "gpu_name": (smi or {}).get("name", ""),
        "vram_total_mb": (smi or {}).get("vram_total_mb", 0),
        "vram_free_mb": (smi or {}).get("vram_free_mb", 0),
        "driver": (smi or {}).get("driver", ""),
        "cuda_dll_dirs": list(_dll_dirs),
        "compute_types_cuda": sorted(supported_compute_types("cuda")) if cuda_ok else [],
        "compute_types_cpu": sorted(supported_compute_types("cpu")),
        "ram": system_memory_mb(),
        "cpu_count": os.cpu_count() or 1,
    }
