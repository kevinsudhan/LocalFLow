"""Silero VAD via onnxruntime.

The ONNX graph signature changed between Silero v4 (separate ``h``/``c`` LSTM
tensors) and v5 (a single packed ``state``), and the file ships under different
names depending on where it came from.  Rather than pin one layout we
introspect the graph inputs once and adapt, which keeps LocalFlow working
against whichever copy of the model is on disk.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import numpy as np

log = logging.getLogger(__name__)

WINDOW = 512          # Silero's mandatory frame size at 16 kHz
SAMPLE_RATE = 16000

# v5 is a *streaming* graph: every frame must be prefixed with the last 64
# samples of the previous one, so the tensor it actually receives is 576 wide.
# Feeding it a bare 512-sample frame is not an error - the graph has dynamic
# axes and runs happily - it simply returns near-zero probabilities for speech,
# which reads downstream as "no speech detected" on perfectly good audio.
CONTEXT = 64


class VadUnavailable(RuntimeError):
    pass


def find_model(models_dir: Path) -> Path | None:
    """Locate silero_vad.onnx, preferring LocalFlow's own copy."""
    candidates = [
        models_dir / "silero_vad.onnx",
        models_dir / "vad" / "silero_vad.onnx",
    ]
    for path in candidates:
        if path.is_file():
            return path
    # faster-whisper ships a copy of the same model in its assets folder.
    try:
        import faster_whisper

        assets = Path(faster_whisper.__file__).parent / "assets"
        for name in ("silero_vad.onnx", "silero_vad_v5.onnx"):
            candidate = assets / name
            if candidate.is_file():
                return candidate
    except Exception:
        pass
    return None


class SileroVad:
    """Frame-level speech probabilities."""

    def __init__(self, model_path: Path, num_threads: int = 1):
        try:
            import onnxruntime as ort
        except Exception as exc:  # pragma: no cover
            raise VadUnavailable("onnxruntime is not installed") from exc

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = num_threads
        opts.intra_op_num_threads = num_threads
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.log_severity_level = 3
        try:
            self.session = ort.InferenceSession(
                str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:
            raise VadUnavailable("Could not load the VAD model: " + str(exc)) from exc

        self.model_path = model_path
        self._input_names = [i.name for i in self.session.get_inputs()]
        self._output_names = [o.name for o in self.session.get_outputs()]
        if "state" in self._input_names:
            self._layout = "v5"
        elif "h" in self._input_names and "c" in self._input_names:
            self._layout = "v4"
        else:
            raise VadUnavailable(
                "Unrecognised VAD model layout: " + ", ".join(self._input_names)
            )
        self.reset()

    def reset(self) -> None:
        if self._layout == "v5":
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
            self._context = np.zeros((1, CONTEXT), dtype=np.float32)
        else:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def _feed(self, frame: np.ndarray) -> float:
        x = frame.reshape(1, -1).astype(np.float32)
        sr = np.array(SAMPLE_RATE, dtype=np.int64)
        if self._layout == "v5":
            x = np.concatenate([self._context, x], axis=1)
            out, self._state = self.session.run(
                None, {"input": x, "state": self._state, "sr": sr}
            )
            self._context = x[:, -CONTEXT:]
        else:
            out, self._h, self._c = self.session.run(
                None, {"input": x, "h": self._h, "c": self._c, "sr": sr}
            )
        return float(np.asarray(out).reshape(-1)[0])

    def probabilities(self, audio: np.ndarray) -> np.ndarray:
        """Speech probability for every 512-sample frame of ``audio``."""
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size < WINDOW:
            return np.zeros(0, dtype=np.float32)
        self.reset()
        n_frames = audio.size // WINDOW
        probs = np.empty(n_frames, dtype=np.float32)
        for i in range(n_frames):
            probs[i] = self._feed(audio[i * WINDOW : (i + 1) * WINDOW])
        return probs

    def stream_probabilities(self, frames: Iterable[np.ndarray]) -> Iterable[float]:
        for frame in frames:
            if frame.size == WINDOW:
                yield self._feed(frame)


class EnergyVad:
    """Fallback VAD used only when the Silero model is missing.

    Adaptive noise floor plus a hysteresis band.  Materially worse than Silero
    on noisy input, which is why it is a fallback and is reported as such in
    the diagnostics panel rather than silently substituted.
    """

    def __init__(self) -> None:
        self._noise = 1e-4

    def reset(self) -> None:
        self._noise = 1e-4

    def probabilities(self, audio: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        n_frames = audio.size // WINDOW
        if n_frames == 0:
            return np.zeros(0, dtype=np.float32)
        frames = audio[: n_frames * WINDOW].reshape(n_frames, WINDOW)
        rms = np.sqrt(np.mean(np.square(frames), axis=1) + 1e-12)
        noise = max(float(np.percentile(rms, 15)), 1e-5)
        ratio = rms / (noise * 4.0)
        return np.clip(ratio, 0.0, 1.0).astype(np.float32)
