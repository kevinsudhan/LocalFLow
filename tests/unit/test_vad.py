"""Silero VAD must actually find speech in speech.

This exists because of a failure that produced no error anywhere: the v5 ONNX
graph has dynamic axes, so feeding it a bare 512-sample frame instead of the
64 samples of context plus 512 it expects runs perfectly happily and returns
near-zero probabilities. Every dictation was transcribed correctly and then
discarded as "No speech detected". Nothing logged a problem - the only visible
symptom was text that never arrived.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from localflow import paths  # noqa: E402
from localflow.audio.resample import resample_to_16k  # noqa: E402
from localflow.config import VadSettings  # noqa: E402
from localflow.vad.segmenter import VadProcessor  # noqa: E402
from localflow.vad.silero import SileroVad, find_model  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "audio"
SPEECH = ("normal", "numbers", "question", "long", "email", "list")


def load_16k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        raw = handle.readframes(handle.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return resample_to_16k(audio, rate) if rate != 16000 else audio


def silero() -> SileroVad:
    model = find_model(paths.MODELS_DIR)
    if model is None:
        pytest.skip("silero_vad.onnx is not installed; run scripts/download_vad.py")
    return SileroVad(model)


@pytest.mark.parametrize("name", SPEECH)
def test_speech_scores_far_above_the_threshold(name: str) -> None:
    path = FIXTURES / f"{name}.wav"
    if not path.is_file():
        pytest.skip(f"missing fixture {name}.wav")
    probs = silero().probabilities(load_16k(path))
    assert probs.size > 0
    # A correctly driven v5 graph saturates on clear speech. The broken one
    # peaked at 0.16 - below the 0.45 decision threshold - so assert with a
    # margin wide enough that a context regression cannot sneak back through.
    assert probs.max() > 0.9, f"{name}: peak speech probability only {probs.max():.3f}"


@pytest.mark.parametrize("name", SPEECH)
def test_processor_keeps_the_audio(name: str) -> None:
    path = FIXTURES / f"{name}.wav"
    if not path.is_file():
        pytest.skip(f"missing fixture {name}.wav")
    processor = VadProcessor(VadSettings(), paths.MODELS_DIR)
    result = processor.process(load_16k(path))
    if processor.backend != "silero":
        pytest.skip("Silero unavailable; energy fallback is not held to this bar")
    assert result.had_speech, f"{name} was rejected as silence"
    assert result.segments, f"{name} produced no speech segments"
    assert result.audio.size >= 1600, f"{name} was trimmed to nothing"


def test_lead_silence_is_trimmed_but_speech_survives() -> None:
    path = FIXTURES / "silence_lead.wav"
    if not path.is_file():
        pytest.skip("missing fixture silence_lead.wav")
    processor = VadProcessor(VadSettings(), paths.MODELS_DIR)
    audio = load_16k(path)
    result = processor.process(audio)
    if processor.backend != "silero":
        pytest.skip("Silero unavailable")
    assert result.had_speech
    assert result.audio.size < audio.size, "leading silence was not trimmed"
