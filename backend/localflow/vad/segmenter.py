"""Turn frame probabilities into speech segments, and trim an utterance.

The goal is never "cut tightly" - it is "drop dead air without ever clipping a
syllable".  Every boundary is padded, and the trimmer refuses to remove more
than a configured amount of lead-in so a mis-firing VAD can never eat the start
of what somebody said.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import VadSettings
from .silero import SAMPLE_RATE, WINDOW, EnergyVad, SileroVad, VadUnavailable, find_model

log = logging.getLogger(__name__)


@dataclass
class Segment:
    start: float          # seconds
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class VadResult:
    audio: np.ndarray
    segments: list[Segment]
    speech_seconds: float
    total_seconds: float
    trimmed_lead: float
    trimmed_tail: float
    backend: str
    had_speech: bool


def segments_from_probs(
    probs: np.ndarray,
    threshold: float,
    min_speech_ms: int,
    min_silence_ms: int,
    pad_ms: int,
    total_seconds: float,
) -> list[Segment]:
    """Hysteresis state machine over per-frame probabilities."""
    if probs.size == 0:
        return []
    frame_s = WINDOW / SAMPLE_RATE
    off_threshold = max(0.12, threshold - 0.15)
    min_speech_frames = max(1, int(min_speech_ms / 1000 / frame_s))
    min_silence_frames = max(1, int(min_silence_ms / 1000 / frame_s))
    pad_s = pad_ms / 1000.0

    raw: list[list[int]] = []
    in_speech = False
    start = 0
    silence_run = 0
    for i, p in enumerate(probs):
        if not in_speech:
            if p >= threshold:
                in_speech = True
                start = i
                silence_run = 0
        else:
            if p < off_threshold:
                silence_run += 1
                if silence_run >= min_silence_frames:
                    end = i - silence_run + 1
                    if end - start >= min_speech_frames:
                        raw.append([start, end])
                    in_speech = False
                    silence_run = 0
            else:
                silence_run = 0
    if in_speech:
        end = probs.size
        if end - start >= min_speech_frames:
            raw.append([start, end])

    segs: list[Segment] = []
    for start_f, end_f in raw:
        s = max(0.0, start_f * frame_s - pad_s)
        e = min(total_seconds, end_f * frame_s + pad_s)
        if segs and s <= segs[-1].end:
            segs[-1] = Segment(segs[-1].start, max(segs[-1].end, e))
        else:
            segs.append(Segment(s, e))
    return segs


class VadProcessor:
    """Loads the best available VAD backend and applies it to utterances."""

    def __init__(self, settings: VadSettings, models_dir: Path):
        self.settings = settings
        self.models_dir = models_dir
        self._engine: SileroVad | EnergyVad | None = None
        self.backend = "none"
        self.load_error = ""

    def load(self) -> None:
        if self._engine is not None:
            return
        path = find_model(self.models_dir)
        if path is not None:
            try:
                self._engine = SileroVad(path)
                self.backend = "silero"
                log.info("Silero VAD loaded from %s", path)
                return
            except VadUnavailable as exc:
                self.load_error = str(exc)
                log.warning("Silero VAD unavailable (%s); using energy fallback", exc)
        else:
            self.load_error = "silero_vad.onnx not found"
            log.warning("Silero VAD model missing; using energy fallback")
        self._engine = EnergyVad()
        self.backend = "energy"

    def unload(self) -> None:
        self._engine = None
        self.backend = "none"

    @property
    def ready(self) -> bool:
        return self._engine is not None

    def reconfigure(self, settings: VadSettings) -> None:
        self.settings = settings

    def analyse(self, audio: np.ndarray) -> np.ndarray:
        self.load()
        assert self._engine is not None
        return self._engine.probabilities(audio)

    def process(self, audio: np.ndarray) -> VadResult:
        """Trim leading/trailing silence and collapse long internal gaps."""
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        total = audio.size / SAMPLE_RATE
        if not self.settings.enabled or audio.size < WINDOW * 2:
            return VadResult(audio, [Segment(0.0, total)], total, total, 0.0, 0.0,
                             self.backend or "disabled", audio.size > 0)

        probs = self.analyse(audio)
        segments = segments_from_probs(
            probs,
            threshold=self.settings.threshold,
            min_speech_ms=self.settings.min_speech_ms,
            min_silence_ms=self.settings.min_silence_ms,
            pad_ms=self.settings.speech_pad_ms,
            total_seconds=total,
        )
        if not segments:
            return VadResult(audio, [], 0.0, total, 0.0, 0.0, self.backend, False)

        max_trim = self.settings.max_trim_lead_ms / 1000.0
        lead = min(segments[0].start, max_trim)
        tail_gap = total - segments[-1].end
        tail = min(max(0.0, tail_gap), max_trim)

        start_idx = int(lead * SAMPLE_RATE)
        end_idx = audio.size - int(tail * SAMPLE_RATE)
        trimmed = audio[start_idx:end_idx] if end_idx > start_idx else audio

        speech = sum(s.duration for s in segments)
        shifted = [Segment(max(0.0, s.start - lead), max(0.0, s.end - lead)) for s in segments]
        return VadResult(
            audio=trimmed,
            segments=shifted,
            speech_seconds=speech,
            total_seconds=total,
            trimmed_lead=lead,
            trimmed_tail=tail,
            backend=self.backend,
            had_speech=True,
        )

    def trailing_silence(self, audio: np.ndarray, window_s: float = 2.0) -> float:
        """Seconds of silence at the end - drives hands-free auto-stop."""
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        take = int(window_s * SAMPLE_RATE)
        if audio.size < WINDOW:
            return 0.0
        tail = audio[-take:] if audio.size > take else audio
        probs = self.analyse(tail)
        if probs.size == 0:
            return 0.0
        frame_s = WINDOW / SAMPLE_RATE
        silent = 0
        for p in probs[::-1]:
            if p >= self.settings.threshold:
                break
            silent += 1
        return silent * frame_s

    def info(self) -> dict:
        return {
            "backend": self.backend,
            "ready": self.ready,
            "error": self.load_error,
            "threshold": self.settings.threshold,
        }
