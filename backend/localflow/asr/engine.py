"""faster-whisper engine with a persistent, warmed model.

The model is loaded exactly once and then held.  Reloading CTranslate2 weights
costs 1-4 s, which would dominate every single dictation, so the engine owns the
model for the lifetime of the process and only releases it when the user asks
(or when a memory-aware unload policy fires).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..config import AsrSettings
from .gpu import prepare_cuda, probe
from .models import CATALOG, resolve_repo, resolve_runtime

log = logging.getLogger(__name__)

MAX_PARTIAL_SECONDS = 14.0

# How long a pass will queue behind another before giving up. A partial that
# cannot start immediately is worthless - the next one is 750 ms away - while
# the final pass is what the user is actually waiting for.
PARTIAL_LOCK_WAIT_S = 0.05
FINAL_LOCK_WAIT_S = 25.0
_PROMPT_TOKEN_BUDGET = 180


class AsrError(RuntimeError):
    def __init__(self, code: str, message: str, detail: str = ""):
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass
class Transcription:
    text: str
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0
    latency_ms: float = 0.0
    segments: list[dict[str, Any]] = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    partial: bool = False


class WhisperEngine:
    def __init__(self, settings: AsrSettings, models_dir: Path):
        self.settings = settings
        self.models_dir = models_dir
        self._model = None
        self._lock = threading.RLock()
        self._partial_lock = threading.Lock()
        # Set the moment a final pass is wanted. Partials are best-effort and
        # must get out of the way: a partial already inside CTranslate2 cannot
        # be interrupted, so the only lever is to stop starting new ones.
        self._final_pending = threading.Event()
        self.loaded_model = ""
        self.loaded_device = ""
        self.loaded_compute = ""
        self.load_error = ""
        self.loading = False
        self.last_language = ""
        self.load_seconds = 0.0
        self.last_used = 0.0
        self._hardware: dict[str, Any] | None = None

    # -- introspection -----------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._model is not None

    def hardware(self, refresh: bool = False) -> dict[str, Any]:
        if self._hardware is None or refresh:
            self._hardware = probe()
        return self._hardware

    def info(self) -> dict[str, Any]:
        spec = CATALOG.get(self.loaded_model or self.settings.model)
        return {
            "ready": self.ready,
            "loading": self.loading,
            "model": self.loaded_model or self.settings.model,
            "device": self.loaded_device,
            "compute_type": self.loaded_compute,
            "error": self.load_error,
            "load_seconds": round(self.load_seconds, 2),
            "last_language": self.last_language,
            "estimated_mb": (
                spec.vram_fp16_mb if (spec and self.loaded_device == "cuda") else
                (spec.ram_int8_mb if spec else 0)
            ),
            "label": spec.label if spec else self.settings.model,
        }

    # -- lifecycle ---------------------------------------------------------
    def ensure_loaded(self, progress: Callable[[str, str], None] | None = None) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            self._load(progress)

    def _load(self, progress: Callable[[str, str], None] | None = None) -> None:
        prepare_cuda()
        hw = self.hardware(refresh=True)
        model_key, device, compute = resolve_runtime(
            self.settings.model, self.settings.device, self.settings.compute_type, hw
        )
        repo = resolve_repo(model_key)
        self.loading = True
        self.load_error = ""
        started = time.perf_counter()
        if progress:
            progress("loading", f"Loading {model_key} on {device.upper()}")

        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            self.loading = False
            raise AsrError(
                "asr_import_failed",
                "The speech engine could not start. Reinstall the LocalFlow Python environment.",
                str(exc),
            ) from exc

        kwargs: dict[str, Any] = {
            "device": device,
            "compute_type": compute,
            "download_root": str(self.models_dir / "whisper"),
        }
        if device == "cpu" and self.settings.cpu_threads > 0:
            kwargs["cpu_threads"] = self.settings.cpu_threads

        try:
            model = _build_model(WhisperModel, repo, kwargs, progress)
        except Exception as exc:
            fallback = self._maybe_fallback(exc, repo, model_key, device, compute, progress)
            if fallback is None:
                self.loading = False
                self.load_error = str(exc)
                raise self._translate_load_error(exc, device, model_key) from exc
            model, device, compute = fallback

        self._model = model
        self.loaded_model = model_key
        self.loaded_device = device
        self.loaded_compute = compute
        self.load_seconds = time.perf_counter() - started
        self.loading = False
        self.last_used = time.time()
        log.info(
            "Whisper ready: %s on %s/%s in %.2fs", model_key, device, compute, self.load_seconds
        )
        if progress:
            progress("ready", f"{model_key} ready on {device.upper()}")
        if self.settings.keep_warm:
            self.warm_up()

    def _maybe_fallback(
        self,
        exc: Exception,
        repo: str,
        model_key: str,
        device: str,
        compute: str,
        progress: Callable[[str, str], None] | None,
    ):
        """A GPU that cannot host the model should degrade, not fail."""
        if device != "cuda":
            return None
        text = str(exc).lower()
        gpu_problem = any(
            k in text
            for k in ("cuda", "cudnn", "cublas", "out of memory", "no kernel", "device", "dll")
        )
        if not gpu_problem:
            return None
        log.warning("GPU load failed (%s); falling back to CPU", exc)
        if progress:
            progress("fallback", "GPU unavailable - loading on CPU instead")
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                repo,
                device="cpu",
                compute_type="int8",
                download_root=str(self.models_dir / "whisper"),
            )
            self.load_error = (
                "GPU inference is unavailable, so LocalFlow is running on the CPU. "
                "Details: " + str(exc)[:200]
            )
            return model, "cpu", "int8"
        except Exception:
            return None

    @staticmethod
    def _translate_load_error(exc: Exception, device: str, model_key: str) -> AsrError:
        text = str(exc)
        low = text.lower()
        if "connection" in low or "resolve" in low or "network" in low or "timed out" in low:
            return AsrError(
                "asr_download_failed",
                f"The {model_key} speech model is not downloaded yet and there is no "
                "internet connection. Connect once to download it, then LocalFlow works offline.",
                text,
            )
        if "out of memory" in low:
            return AsrError(
                "asr_out_of_memory",
                f"Not enough {'VRAM' if device == 'cuda' else 'RAM'} for the {model_key} model. "
                "Pick a smaller model in Settings > Speech Recognition.",
                text,
            )
        if "cudnn" in low or "cublas" in low:
            return AsrError(
                "asr_cuda_missing",
                "CUDA libraries are missing. Run scripts/setup-gpu.ps1, or switch the "
                "device to CPU in Settings > Speech Recognition.",
                text,
            )
        return AsrError("asr_load_failed", "The speech model could not be loaded.", text)

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self.loaded_model = ""
            self.loaded_device = ""
            self.loaded_compute = ""
        import gc

        gc.collect()
        log.info("Whisper model unloaded")

    def reconfigure(self, settings: AsrSettings) -> bool:
        """Returns True when the change requires a reload."""
        needs_reload = (
            settings.model != self.settings.model
            or settings.device != self.settings.device
            or settings.compute_type != self.settings.compute_type
            or settings.cpu_threads != self.settings.cpu_threads
        )
        self.settings = settings
        if needs_reload and self._model is not None:
            self.unload()
        return needs_reload

    def warm_up(self) -> float:
        """Run one tiny inference so the first real dictation is not the slow one."""
        if self._model is None:
            return 0.0
        started = time.perf_counter()
        try:
            silence = np.zeros(16000, dtype=np.float32)
            segments, _ = self._model.transcribe(
                silence, language="en", beam_size=1, without_timestamps=True
            )
            list(segments)
        except Exception:
            log.debug("Warm-up failed", exc_info=True)
        elapsed = time.perf_counter() - started
        log.info("Warm-up completed in %.2fs", elapsed)
        return elapsed

    # -- transcription -----------------------------------------------------
    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        initial_prompt: str | None = None,
        partial: bool = False,
    ) -> Transcription:
        if self._model is None:
            self.ensure_loaded()
        if self._model is None:
            raise AsrError("asr_not_ready", "The speech model is not loaded.")

        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size < 160:
            return Transcription(text="", partial=partial)

        lang = self._resolve_language(language, partial)
        started = time.perf_counter()
        options: dict[str, Any] = {
            "language": lang,
            "task": self.settings.task,
            "beam_size": self.settings.partial_beam_size if partial else self.settings.beam_size,
            "temperature": self.settings.temperature,
            "condition_on_previous_text": (
                False if partial else self.settings.condition_on_previous_text
            ),
            "without_timestamps": partial,
            "vad_filter": False,
            "word_timestamps": False,
        }
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        if partial:
            options["best_of"] = 1
            options["no_speech_threshold"] = 0.7

        # The final pass is authoritative and the user is waiting on it with a
        # frozen HUD; a partial is disposable. Waiting forever for the engine
        # lock turned a slow partial into a two-minute hang with no way out, so
        # the wait is bounded and failure is reported rather than endured.
        timeout = PARTIAL_LOCK_WAIT_S if partial else FINAL_LOCK_WAIT_S
        if not partial:
            self._final_pending.set()
        try:
            if not self._lock.acquire(timeout=timeout):
                if partial:
                    return Transcription(text="", partial=True)
                raise AsrError(
                    "asr_busy",
                    "The speech engine was still busy with the live preview. "
                    "Try again - and if this keeps happening, turn off live "
                    "previews in Settings > Speech.",
                    f"waited {timeout:.0f}s for the previous pass to finish",
                )
            try:
                segments_iter, info = self._model.transcribe(audio, **options)
                segments = list(segments_iter)
            except Exception as exc:
                raise self._translate_runtime_error(exc) from exc
            finally:
                self._lock.release()
        finally:
            if not partial:
                self._final_pending.clear()

        text = "".join(s.text for s in segments).strip()
        detected = getattr(info, "language", "") or lang or ""
        if detected and not partial:
            self.last_language = detected
        elif detected and not self.last_language:
            self.last_language = detected

        logprobs = [s.avg_logprob for s in segments if s.avg_logprob is not None]
        nosp = [s.no_speech_prob for s in segments if s.no_speech_prob is not None]
        self.last_used = time.time()
        return Transcription(
            text=text,
            language=detected,
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            duration=float(getattr(info, "duration", audio.size / 16000.0)),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            segments=[
                {"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments
            ],
            avg_logprob=float(sum(logprobs) / len(logprobs)) if logprobs else 0.0,
            no_speech_prob=float(sum(nosp) / len(nosp)) if nosp else 0.0,
            partial=partial,
        )

    def _resolve_language(self, language: str | None, partial: bool) -> str | None:
        configured = language if language is not None else self.settings.language
        if configured and configured != "auto":
            return configured
        # Auto-detection costs an extra encoder pass; on partials we reuse the
        # language the previous pass settled on.
        if partial and self.last_language:
            return self.last_language
        return None

    def transcribe_partial(
        self, audio: np.ndarray, initial_prompt: str | None = None
    ) -> Transcription | None:
        """Best-effort partial.  Returns None if a pass is already running."""
        if self._final_pending.is_set():
            # A final pass is queued or running; starting another partial only
            # lengthens the queue the user is waiting behind.
            return None
        if not self._partial_lock.acquire(blocking=False):
            return None
        try:
            max_samples = int(MAX_PARTIAL_SECONDS * 16000)
            if audio.size > max_samples:
                audio = audio[-max_samples:]
            return self.transcribe(audio, initial_prompt=initial_prompt, partial=True)
        except AsrError:
            raise
        except Exception:
            log.debug("Partial transcription failed", exc_info=True)
            return None
        finally:
            self._partial_lock.release()

    @staticmethod
    def _translate_runtime_error(exc: Exception) -> AsrError:
        text = str(exc)
        low = text.lower()
        if "out of memory" in low:
            return AsrError(
                "asr_out_of_memory",
                "The GPU ran out of memory during transcription. Close other GPU apps or "
                "choose a smaller model.",
                text,
            )
        if "cudnn" in low or "cublas" in low:
            return AsrError(
                "asr_cuda_error",
                "A CUDA error interrupted transcription. Switching to CPU in Settings > "
                "Speech Recognition will keep dictation working.",
                text,
            )
        return AsrError("asr_failed", "Transcription failed.", text)


def build_initial_prompt(terms: list[str], style_hint: str = "") -> str:
    """Bias Whisper toward the user's own vocabulary.

    Whisper's ``initial_prompt`` is capped at 224 tokens; overflowing it silently
    drops the tail, so we budget conservatively and keep the highest-value terms.
    """
    if not terms:
        return style_hint
    picked: list[str] = []
    budget = _PROMPT_TOKEN_BUDGET
    for term in terms:
        cost = max(1, len(term) // 3)
        if budget - cost <= 0:
            break
        picked.append(term)
        budget -= cost
    if not picked:
        return style_hint
    prompt = "Glossary: " + ", ".join(picked) + "."
    return (style_hint + " " + prompt).strip() if style_hint else prompt


# Windows refuses to create symbolic links without administrator rights or
# Developer Mode, failing with ERROR_PRIVILEGE_NOT_HELD (WinError 1314).
_ERROR_PRIVILEGE_NOT_HELD = 1314


def _disable_hf_symlinks() -> bool:
    """Make Hugging Face copy blobs into the snapshot rather than link them.

    huggingface_hub does have a copy fallback for machines without symlink
    permission, but it is guarded on ``PermissionError`` while WinError 1314
    surfaces as a plain ``OSError`` - so the download completes, the link is
    never made, and the *load* then dies on a ``config.json`` that is not
    there. Forcing the copy path costs disk (the blob is stored twice), so it
    is only done after an actual failure; machines where symlinks work keep
    the cheaper layout.
    """
    try:
        from huggingface_hub import file_download

        file_download.are_symlinks_supported = lambda cache_dir=None: False
        return True
    except Exception:  # pragma: no cover - depends on huggingface_hub internals
        log.warning("Could not switch Hugging Face to copy mode", exc_info=True)
        return False


def _build_model(whisper_model, repo: str, kwargs: dict, progress=None):
    """Construct the model, surviving Windows' refusal to create symlinks."""
    try:
        return whisper_model(repo, **kwargs)
    except OSError as exc:
        if getattr(exc, "winerror", None) != _ERROR_PRIVILEGE_NOT_HELD:
            raise
        if not _disable_hf_symlinks():
            raise
        log.warning("Windows blocked the model symlinks; retrying with file copies")
        if progress:
            progress("loading", "Finishing the download")
        return whisper_model(repo, **kwargs)
