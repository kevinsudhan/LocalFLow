"""Dictation session lifecycle.

One session spans hotkey-down to text-inserted.  It owns the recording, the
streaming partial transcripts, and the final transcribe + process pass.

Partial transcripts run on a worker thread against a snapshot of the audio
captured so far.  They are explicitly best-effort: a partial is skipped rather
than queued if one is still running, and the final pass never reuses a partial
result.  Showing the user progress must not cost final accuracy.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)


class SessionState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    INSERTING = "inserting"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class SessionTimings:
    started_at: float = 0.0
    stopped_at: float = 0.0
    record_ms: float = 0.0
    vad_ms: float = 0.0
    asr_ms: float = 0.0
    process_ms: float = 0.0
    llm_ms: float = 0.0
    total_ms: float = 0.0
    first_partial_ms: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "record_ms": round(self.record_ms, 1),
            "vad_ms": round(self.vad_ms, 1),
            "asr_ms": round(self.asr_ms, 1),
            "process_ms": round(self.process_ms, 1),
            "llm_ms": round(self.llm_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "first_partial_ms": round(self.first_partial_ms, 1),
        }


@dataclass
class SessionResult:
    session_id: str
    text: str = ""
    insert_text: str = ""
    raw_transcript: str = ""
    deterministic_text: str = ""
    language: str = ""
    used_llm: bool = False
    llm_model: str = ""
    command: dict | None = None
    snippet: dict | None = None
    replace_selection: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    had_speech: bool = True
    cancelled: bool = False


class DictationSession:
    """Recording and transcription for a single utterance."""

    def __init__(
        self,
        session_id: str,
        capture,
        vad,
        asr,
        emit: Callable[[str, dict], None],
        partial_interval_ms: int = 750,
        partial_min_audio_ms: int = 900,
        streaming: bool = True,
        initial_prompt: str = "",
        language: str | None = None,
        auto_stop_silence_ms: int = 0,
    ) -> None:
        self.id = session_id
        self.capture = capture
        self.vad = vad
        self.asr = asr
        self.emit = emit
        self.partial_interval = max(0.25, partial_interval_ms / 1000.0)
        self.partial_min_audio = partial_min_audio_ms / 1000.0
        self.streaming = streaming
        self.initial_prompt = initial_prompt
        self.language = language
        # > 0 enables hands-free: the session ends itself after this much silence.
        self.auto_stop_silence_ms = auto_stop_silence_ms

        self.state = SessionState.IDLE
        self.timings = SessionTimings()
        self._stop_event = threading.Event()
        self._partial_thread: threading.Thread | None = None
        self._silence_thread: threading.Thread | None = None
        self._last_partial = ""
        self._cancelled = False
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.timings.started_at = time.perf_counter()
        self.capture.begin()
        self._set_state(SessionState.LISTENING)
        if self.streaming:
            self._partial_thread = threading.Thread(
                target=self._partial_loop, name=f"partials-{self.id}", daemon=True
            )
            self._partial_thread.start()
        if self.auto_stop_silence_ms > 0:
            self._silence_thread = threading.Thread(
                target=self._silence_loop, name=f"silence-{self.id}", daemon=True
            )
            self._silence_thread.start()

    # -- hands-free auto-stop ---------------------------------------------
    def _silence_loop(self) -> None:
        """End the dictation once the speaker has stopped talking.

        Driven by the VAD rather than by raw level, so a quiet speaker is not
        cut off mid-sentence and a noisy room does not keep the session open
        forever. A grace period at the start stops it firing before the user
        has drawn breath.
        """
        threshold = self.auto_stop_silence_ms / 1000.0
        grace = max(1.0, threshold)
        heard_speech = False

        while not self._stop_event.is_set():
            self._stop_event.wait(0.3)
            if self._stop_event.is_set():
                return
            elapsed = time.perf_counter() - self.timings.started_at
            if elapsed < grace:
                continue
            try:
                audio = self.capture.snapshot()
                if audio.size < 8000:
                    continue
                silence = self.vad.trailing_silence(audio, window_s=threshold + 1.0)
            except Exception:
                log.debug("Silence check failed", exc_info=True)
                continue

            # Require having heard something first, so an accidental start in a
            # silent room does not immediately stop and insert nothing.
            if not heard_speech:
                if silence < threshold * 0.5:
                    heard_speech = True
                continue
            if silence >= threshold:
                log.info("Hands-free auto-stop after %.1fs of silence", silence)
                self.emit(
                    "session.autostop",
                    {"session_id": self.id, "silence_seconds": round(silence, 2)},
                )
                return

    def cancel(self) -> None:
        self._cancelled = True
        self._stop_event.set()
        self.capture.cancel()
        self._set_state(SessionState.CANCELLED)

    def stop_recording(self) -> np.ndarray:
        """End capture and return the raw utterance audio."""
        self._stop_event.set()
        audio = self.capture.end()
        self.timings.stopped_at = time.perf_counter()
        self.timings.record_ms = (self.timings.stopped_at - self.timings.started_at) * 1000.0
        return audio

    def join_partials(self, timeout: float = 1.5) -> None:
        thread = self._partial_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # -- transcription -----------------------------------------------------
    def transcribe(self, audio: np.ndarray) -> tuple[str, dict[str, Any]]:
        """VAD-trim then run the authoritative transcription pass."""
        self._set_state(SessionState.PROCESSING)

        vad_started = time.perf_counter()
        vad_result = self.vad.process(audio)
        self.timings.vad_ms = (time.perf_counter() - vad_started) * 1000.0

        meta: dict[str, Any] = {
            "vad_backend": vad_result.backend,
            "speech_seconds": round(vad_result.speech_seconds, 2),
            "total_seconds": round(vad_result.total_seconds, 2),
            "trimmed_lead": round(vad_result.trimmed_lead, 2),
            "trimmed_tail": round(vad_result.trimmed_tail, 2),
            "had_speech": vad_result.had_speech,
        }
        if not vad_result.had_speech or vad_result.audio.size < 1600:
            meta["segments"] = []
            return "", meta

        transcription = self.asr.transcribe(
            vad_result.audio,
            language=self.language,
            initial_prompt=self.initial_prompt or None,
        )
        self.timings.asr_ms = transcription.latency_ms
        meta.update(
            {
                "segments": transcription.segments,
                "language": transcription.language,
                "language_probability": round(transcription.language_probability, 3),
                "avg_logprob": round(transcription.avg_logprob, 3),
                "no_speech_prob": round(transcription.no_speech_prob, 3),
                "audio_seconds": round(vad_result.audio.size / 16000.0, 2),
            }
        )
        return transcription.text, meta

    # -- partial transcripts ----------------------------------------------
    def _partial_loop(self) -> None:
        next_run = time.perf_counter() + self.partial_min_audio
        while not self._stop_event.is_set():
            now = time.perf_counter()
            if now < next_run:
                self._stop_event.wait(min(0.05, next_run - now))
                continue
            next_run = now + self.partial_interval
            try:
                audio = self.capture.snapshot()
            except Exception:
                log.debug("Snapshot failed", exc_info=True)
                continue
            if audio.size < int(self.partial_min_audio * 16000):
                continue
            if self._stop_event.is_set():
                break
            try:
                result = self.asr.transcribe_partial(
                    audio, initial_prompt=self.initial_prompt or None
                )
            except Exception:
                log.debug("Partial transcription failed", exc_info=True)
                continue
            if result is None or self._stop_event.is_set():
                continue
            text = (result.text or "").strip()
            if not text or text == self._last_partial:
                continue
            self._last_partial = text
            if not self.timings.first_partial_ms:
                self.timings.first_partial_ms = (
                    time.perf_counter() - self.timings.started_at
                ) * 1000.0
            self.emit(
                "session.partial",
                {
                    "session_id": self.id,
                    "text": text,
                    "final": False,
                    "elapsed_ms": round((time.perf_counter() - self.timings.started_at) * 1000, 1),
                },
            )

    # -- helpers -----------------------------------------------------------
    def _set_state(self, state: SessionState) -> None:
        with self._lock:
            self.state = state
        self.emit("session.state", {"session_id": self.id, "state": state.value})

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def last_partial(self) -> str:
        return self._last_partial
