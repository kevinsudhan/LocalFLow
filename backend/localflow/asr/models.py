"""Whisper model catalogue and hardware-aware selection.

VRAM figures are measured working-set numbers for CTranslate2, not parameter
counts: they include activations and the KV cache for a typical 30 s window,
which is what actually decides whether a model fits on a 6 GB laptop GPU.
"""
from __future__ import annotations

from dataclasses import dataclass

from .gpu import probe


@dataclass(frozen=True)
class ModelSpec:
    key: str
    repo: str
    label: str
    params: str
    download_mb: int
    vram_fp16_mb: int
    ram_int8_mb: int
    multilingual: bool
    quality: int          # 1-5, relative WER on general English
    speed: int            # 1-5, higher is faster
    note: str = ""


# VRAM figures for small, medium and large-v3-turbo are *measured* on an
# RTX 3060 6 GB at float16 with a warmed model (benchmarks/compare_models.py);
# the rest are extrapolated from parameter count. Quality and speed ratings for
# those three also come from that run, on speech degraded with pink noise and
# attenuation - clean audio does not separate the models at all.
CATALOG: dict[str, ModelSpec] = {
    m.key: m
    for m in (
        ModelSpec("tiny", "Systran/faster-whisper-tiny", "Tiny", "39M", 75, 400, 190,
                  True, 1, 5, "Fastest. Noticeably weaker on names and numbers."),
        ModelSpec("base", "Systran/faster-whisper-base", "Base", "74M", 145, 600, 290,
                  True, 2, 5, "Reasonable CPU-only default."),
        ModelSpec("small", "Systran/faster-whisper-small", "Small", "244M", 484, 755, 700,
                  True, 3, 5, "Fastest usable model. Struggles in a noisy room."),
        ModelSpec("medium", "Systran/faster-whisper-medium", "Medium", "769M", 1530, 1992, 1900,
                  True, 4, 2, "Accuracy of Turbo, but noticeably slower."),
        ModelSpec("large-v3", "Systran/faster-whisper-large-v3", "Large v3", "1550M", 3090,
                  3600, 3800, True, 5, 1, "Best accuracy, but leaves little VRAM for the LLM."),
        ModelSpec("large-v3-turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                  "Large v3 Turbo", "809M", 1620, 2120, 2100, True, 5, 4,
                  "Recommended. Large-v3 accuracy with a 4-layer decoder - 38% fewer "
                  "errors than Small on noisy speech, and faster than Medium."),
        ModelSpec("distil-small.en", "Systran/faster-distil-whisper-small.en",
                  "Distil Small (English)", "166M", 332, 900, 560, False, 3, 5,
                  "English only. Very fast."),
        ModelSpec("distil-large-v3", "Systran/faster-distil-whisper-large-v3",
                  "Distil Large v3 (English)", "756M", 1510, 2000, 1950, False, 4, 4,
                  "English only. Similar speed to Turbo."),
    )
}

DEFAULT_MODEL = "large-v3-turbo"

# Headroom reserved for the local language model and the desktop compositor.
#
# Deliberately smaller than a resident 3B model (~2.9 GB measured): Whisper runs
# on every single dictation and must stay resident, while the language model
# runs on roughly one in six and Ollama evicts it when `keep_alive` expires.
# When they do overlap, Ollama offloads layers to the CPU - which costs latency
# on that one dictation, not accuracy on all of them.
LLM_VRAM_RESERVE_MB = 1800

# Left to the desktop compositor, the browser and everything else the user
# has open. Subtracted from total VRAM rather than trusting the free figure.
DESKTOP_RESERVE_MB = 1100


def resolve_repo(key: str) -> str:
    spec = CATALOG.get(key)
    return spec.repo if spec else key


def assignable_vram_mb(hardware: dict) -> int:
    """VRAM LocalFlow may plan around.

    Deliberately *not* just "currently free": by the time this is asked, the
    speech model LocalFlow itself loaded is already occupying VRAM, so reading
    the free figure would recommend a smaller model every time one is
    loaded - and the recommendation would drift downward on every restart.
    Planning against total capacity minus a desktop allowance is stable.
    """
    free = int(hardware.get("vram_free_mb") or 0)
    total = int(hardware.get("vram_total_mb") or 0)
    if not total:
        return free
    return max(free, total - DESKTOP_RESERVE_MB)


def recommend(hardware: dict | None = None, multilingual: bool = True) -> dict:
    """Choose a model/device/compute-type triple that will actually fit."""
    hw = hardware or probe()
    vram = assignable_vram_mb(hw)
    cuda = bool(hw.get("cuda_available"))
    ram_avail = hw.get("ram", {}).get("available", 0)

    if cuda and vram >= 1024:
        # Accuracy first, within a budget that still leaves room for the
        # editing model. Ordered best-accuracy-per-millisecond first.
        budget = max(0, vram - LLM_VRAM_RESERVE_MB)
        for key in ("large-v3", "large-v3-turbo", "medium", "small", "base", "tiny"):
            spec = CATALOG[key]
            if not spec.multilingual and multilingual:
                continue
            if spec.vram_fp16_mb <= budget:
                return {
                    "model": key,
                    "device": "cuda",
                    "compute_type": "float16",
                    "reason": (
                        f"{spec.label} is the most accurate model that fits in {budget} MB "
                        f"of VRAM while leaving {LLM_VRAM_RESERVE_MB} MB for the local "
                        "language model."
                    ),
                }
        # Nothing fits comfortably: quantise rather than drop to a tiny model,
        # because int8 costs far less accuracy than four model sizes would.
        for key in ("large-v3-turbo", "small", "base"):
            spec = CATALOG[key]
            if spec.vram_fp16_mb * 0.6 <= max(budget, vram - 1200):
                return {
                    "model": key,
                    "device": "cuda",
                    "compute_type": "int8_float16",
                    "reason": (
                        f"VRAM is tight, so {spec.label} runs quantised on the GPU - "
                        "still more accurate than dropping to a smaller model."
                    ),
                }
        return {
            "model": "base",
            "device": "cuda",
            "compute_type": "int8_float16",
            "reason": "Very limited VRAM; using a quantised Base model on the GPU.",
        }

    for key in ("small", "base", "tiny"):
        spec = CATALOG[key]
        if not spec.multilingual and multilingual:
            continue
        if spec.ram_int8_mb <= max(ram_avail - 1500, 0):
            return {
                "model": key,
                "device": "cpu",
                "compute_type": "int8",
                "reason": f"No usable GPU detected; {spec.label} INT8 on CPU keeps latency sane.",
            }
    return {
        "model": "tiny",
        "device": "cpu",
        "compute_type": "int8",
        "reason": "Very limited memory; using the Tiny model on CPU.",
    }


def resolve_runtime(
    model: str, device: str, compute_type: str, hardware: dict | None = None
) -> tuple[str, str, str]:
    """Turn ``auto`` settings into a concrete (model, device, compute_type)."""
    hw = hardware or probe()
    cuda = bool(hw.get("cuda_available"))

    if model in ("", "auto"):
        model = recommend(hw)["model"]

    if device == "auto":
        device = "cuda" if cuda else "cpu"
    elif device == "cuda" and not cuda:
        device = "cpu"

    if compute_type == "auto":
        if device == "cuda":
            supported = set(hw.get("compute_types_cuda") or [])
            spec = CATALOG.get(model)
            vram = assignable_vram_mb(hw)
            tight = bool(spec and vram and spec.vram_fp16_mb > max(0, vram - 1200))
            if tight and "int8_float16" in supported:
                compute_type = "int8_float16"
            elif "float16" in supported:
                compute_type = "float16"
            else:
                compute_type = "int8"
        else:
            supported = set(hw.get("compute_types_cpu") or [])
            compute_type = "int8" if "int8" in supported else "float32"
    return model, device, compute_type


def catalog_payload(hardware: dict | None = None) -> list[dict]:
    hw = hardware or probe()
    vram = hw.get("vram_total_mb", 0)
    ram = hw.get("ram", {}).get("total", 0)
    out = []
    for spec in CATALOG.values():
        fits_gpu = bool(hw.get("cuda_available")) and spec.vram_fp16_mb <= max(0, vram - 900)
        fits_cpu = spec.ram_int8_mb <= max(0, ram - 2000)
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "repo": spec.repo,
                "params": spec.params,
                "download_mb": spec.download_mb,
                "vram_fp16_mb": spec.vram_fp16_mb,
                "ram_int8_mb": spec.ram_int8_mb,
                "multilingual": spec.multilingual,
                "quality": spec.quality,
                "speed": spec.speed,
                "note": spec.note,
                "fits_gpu": fits_gpu,
                "fits_cpu": fits_cpu,
            }
        )
    return out
