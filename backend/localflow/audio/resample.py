"""Sample-rate conversion down to the 16 kHz Whisper expects.

We always *ask* the device for 16 kHz first, which WASAPI shared mode almost
always grants.  This module is the fallback for the devices that refuse.
A windowed-sinc FIR low-pass before decimation keeps aliasing out of the band
Whisper actually listens to; plain linear interpolation from 48 kHz audibly
degrades sibilants and costs real word error rate.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

TARGET_RATE = 16000


@lru_cache(maxsize=8)
def _lowpass_kernel(cutoff_ratio: float, taps: int = 129) -> np.ndarray:
    """Windowed-sinc low-pass. ``cutoff_ratio`` is cutoff / input_rate."""
    n = np.arange(taps) - (taps - 1) / 2.0
    with np.errstate(invalid="ignore"):
        h = 2 * cutoff_ratio * np.sinc(2 * cutoff_ratio * n)
    h *= np.hamming(taps)
    total = h.sum()
    if total != 0:
        h /= total
    return h.astype(np.float32)


def resample_to_16k(audio: np.ndarray, source_rate: int) -> np.ndarray:
    """Convert mono float32 ``audio`` from ``source_rate`` to 16 kHz."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if source_rate == TARGET_RATE or audio.size == 0:
        return audio

    if source_rate > TARGET_RATE:
        # Guard the new Nyquist (8 kHz) with a little margin.
        kernel = _lowpass_kernel(cutoff_ratio=(TARGET_RATE / 2 * 0.92) / source_rate)
        audio = np.convolve(audio, kernel, mode="same").astype(np.float32)

    gcd = math.gcd(int(source_rate), TARGET_RATE)
    if source_rate % TARGET_RATE == 0:
        # Integer decimation (48000 -> 16000, 32000 -> 16000).
        return np.ascontiguousarray(audio[:: source_rate // TARGET_RATE])

    # Rational rates such as 44100 -> 16000: interpolate on the already
    # band-limited signal, which is safe because aliasing is gone.
    duration = audio.size / float(source_rate)
    out_len = max(1, int(round(duration * TARGET_RATE)))
    src_idx = np.linspace(0.0, audio.size - 1, out_len, dtype=np.float64)
    out = np.interp(src_idx, np.arange(audio.size, dtype=np.float64), audio)
    del gcd
    return out.astype(np.float32)


def to_mono(audio: np.ndarray) -> np.ndarray:
    """Collapse an (n, channels) block to mono."""
    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 1:
        return arr
    if arr.shape[1] == 1:
        return arr[:, 0]
    return arr.mean(axis=1).astype(np.float32)
