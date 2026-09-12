"""Microphone capture with a rolling pre-roll buffer.

The input stream is held open while LocalFlow is armed so that pressing the
hotkey starts *retaining* audio that is already in flight rather than opening a
device (50-150 ms on Windows, which is exactly the first syllable).  Audio in
the pre-roll ring is continuously overwritten and never written to disk.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from ..config import AudioSettings
from .devices import AudioUnavailable, candidates_for
from .devices import refresh as refresh_devices
from .resample import TARGET_RATE, resample_to_16k, to_mono

log = logging.getLogger(__name__)

LevelCallback = Callable[[float, float], None]
ErrorCallback = Callable[[str, str], None]

# Above the dither floor of a dead endpoint, below the noise floor of a live
# one. Measured on this class of hardware: an unplugged Realtek jack peaks
# around 6e-05, a working microphone in a quiet room around 1.5e-03.
SILENCE_FLOOR = 4e-4


class MicrophoneError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AudioCapture:
    """Owns the PortAudio input stream and the capture buffers."""

    def __init__(
        self,
        settings: AudioSettings,
        on_level: LevelCallback | None = None,
        on_error: ErrorCallback | None = None,
        on_limit: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self._on_level = on_level
        self._on_error = on_error
        self._on_limit = on_limit

        self._stream = None
        self._stream_rate = TARGET_RATE
        self._lock = threading.RLock()
        self._ring: deque[np.ndarray] = deque()
        self._ring_samples = 0
        self._captured: list[np.ndarray] = []
        self._captured_samples = 0
        self._capturing = False
        self._level = 0.0
        self._peak = 0.0
        self._limit_fired = False
        self._device_id: int | None = None
        self._device_name = ""
        self._device_host_api = ""
        self._device_is_alias = False
        self._requested_name = ""
        self._opened_at = 0.0
        self._peak_since_open = 0.0
        self._started_at = 0.0
        self._last_level_emit = 0.0
        self._overflows = 0

    # -- properties --------------------------------------------------------
    @property
    def is_streaming(self) -> bool:
        return self._stream is not None and getattr(self._stream, "active", False)

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    @property
    def level(self) -> float:
        return self._level

    @property
    def device_id(self) -> int | None:
        return self._device_id

    @property
    def device_name(self) -> str:
        """The device actually being recorded from, not the one configured."""
        return self._device_name

    @property
    def device_host_api(self) -> str:
        return self._device_host_api

    @property
    def device_is_alias(self) -> bool:
        """Whether the open device is a Windows router rather than a microphone."""
        return self._device_is_alias

    @property
    def silent_seconds(self) -> float:
        """How long the open device has delivered nothing but digital silence.

        An endpoint that opens and returns zeros is worse than one that fails
        to open: the application looks healthy and records nothing. Reporting
        this lets the interface say so instead of showing a dead meter.
        """
        if not self.is_streaming or self._peak_since_open > SILENCE_FLOOR:
            return 0.0
        return max(0.0, time.perf_counter() - self._opened_at)

    @property
    def sample_rate(self) -> int:
        return TARGET_RATE

    def captured_seconds(self) -> float:
        with self._lock:
            return self._captured_samples / float(TARGET_RATE)

    # -- stream lifecycle --------------------------------------------------
    def open(self) -> None:
        """Open the input stream.  Idempotent."""
        if self.is_streaming:
            return
        try:
            import sounddevice as sd
        except Exception as exc:
            raise MicrophoneError("audio_backend", str(exc)) from exc

        try:
            self._open_once(sd)
            return
        except MicrophoneError as first:
            if first.code == "no_microphone":
                raise
            # Every candidate failed, which is what plugging in a headset after
            # launch looks like: PortAudio is still holding the device list it
            # snapshotted at start-up, so the indices point at endpoints
            # Windows has since renumbered. Re-enumerate and try once more.
            log.warning("No microphone opened (%s); re-enumerating", first)
            refresh_devices()
            self._open_once(sd)

    def _open_once(self, sd) -> None:  # noqa: ANN001
        candidates = candidates_for(self.settings.device_id, self.settings.device_name)
        if not candidates:
            raise MicrophoneError(
                "no_microphone", "No microphone was found. Connect one and try again."
            )

        wanted = candidates[0]
        last_error: Exception | None = None
        for index, device in enumerate(candidates):
            try:
                rate, channels = self._negotiate(sd, device["id"])
                stream = sd.InputStream(
                    device=device["id"],
                    channels=channels,
                    samplerate=rate,
                    blocksize=max(128, int(self.settings.blocksize)),
                    dtype="float32",
                    latency="low",
                    callback=self._callback,
                    finished_callback=self._on_stream_finished,
                )
                stream.start()
            except Exception as exc:
                last_error = exc
                log.debug("Device %s (%s) unavailable: %s", device["id"], device["name"], exc)
                continue

            self._stream = stream
            self._device_id = device["id"]
            self._device_name = device["name"]
            self._device_host_api = device["host_api"]
            self._device_is_alias = bool(device["alias"])
            self._requested_name = wanted["name"]
            self._stream_rate = rate
            self._overflows = 0
            self._opened_at = time.perf_counter()
            self._peak_since_open = 0.0
            log.info(
                "Microphone open: [%s] %s (%s) rate=%s ch=%s",
                device["id"], device["name"], device["host_api"], rate, channels,
            )
            if index > 0:
                # Recording from something other than what was asked for is
                # never acceptable in silence - the meter would simply sit at
                # zero with no explanation.
                self._emit_error(
                    "mic_fallback",
                    f"“{wanted['name']}” could not be opened, so LocalFlow is "
                    f"recording from “{device['name']}” instead.",
                )
            if device["alias"]:
                # Routers record from "whatever Windows thinks is default",
                # which is a guess wearing a device's clothes.
                self._emit_error(
                    "mic_router",
                    f"“{device['name']}” is a Windows audio router, not a "
                    "microphone. Pick a specific device in Settings › Audio if "
                    "nothing is being heard.",
                )
            return

        self._stream = None
        raise MicrophoneError(
            "mic_open_failed",
            _friendly(last_error) if last_error else "No microphone could be opened.",
        )

    def _negotiate(self, sd, device: int) -> tuple[int, int]:
        """Pick the best (rate, channels) the device will actually accept."""
        try:
            info = sd.query_devices(device)
            max_ch = max(1, int(info.get("max_input_channels", 1)))
            default_rate = int(info.get("default_samplerate", 48000) or 48000)
        except Exception as exc:
            raise MicrophoneError("mic_query_failed", _friendly(exc)) from exc

        channels = 1 if max_ch >= 1 else max_ch
        for rate in (TARGET_RATE, default_rate, 48000, 44100):
            try:
                sd.check_input_settings(
                    device=device, channels=channels, samplerate=rate, dtype="float32"
                )
                return rate, channels
            except Exception:
                continue
        return default_rate, channels

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # pragma: no cover - teardown best effort
                log.debug("Error closing stream", exc_info=True)
        with self._lock:
            self._ring.clear()
            self._ring_samples = 0
            self._captured.clear()
            self._captured_samples = 0
            self._capturing = False
        self._level = 0.0
        self._device_name = ""
        self._device_host_api = ""
        self._device_is_alias = False
        self._peak_since_open = 0.0

    def reconfigure(self, settings: AudioSettings) -> None:
        """Apply new audio settings, reopening the device if it changed.

        Never leaves LocalFlow without a microphone: if the new device cannot
        be opened, the previous one is restored. Losing audio entirely because
        a settings change failed is far worse than the change not applying.
        """
        was_open = self.is_streaming
        previous = self.settings
        device_changed = (
            settings.device_id != self.settings.device_id
            or settings.device_name != self.settings.device_name
            or settings.blocksize != self.settings.blocksize
        )
        self.settings = settings
        if not (device_changed and was_open):
            return

        self.close()
        try:
            self.open()
        except MicrophoneError as exc:
            log.warning("New microphone failed (%s); restoring the previous one", exc)
            self.settings = previous
            try:
                self.open()
            except MicrophoneError:
                log.error("Could not restore the previous microphone either")
            raise

    def _on_stream_finished(self) -> None:
        # PortAudio calls this when the device vanishes mid-stream.
        if self._stream is not None and self._capturing:
            self._emit_error(
                "mic_disconnected",
                "The microphone was disconnected. Reconnect it or pick another device.",
            )

    # -- capture control ---------------------------------------------------
    def begin(self) -> None:
        """Start retaining audio, seeded with the pre-roll ring."""
        if not self.is_streaming:
            self.open()
        with self._lock:
            pre = list(self._ring)
            self._captured = pre
            self._captured_samples = sum(b.size for b in pre)
            self._capturing = True
            self._limit_fired = False
            self._started_at = time.perf_counter()

    def snapshot(self) -> np.ndarray:
        """Copy of everything captured so far (used for partial transcripts)."""
        with self._lock:
            if not self._captured:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._captured).astype(np.float32, copy=False)

    def end(self, keep_tail_ms: int | None = None) -> np.ndarray:
        """Stop retaining audio and return the full utterance.

        A short tail keeps recording past the key release so the last syllable
        (which is still travelling through the driver buffer) is not clipped.
        """
        tail = self.settings.post_buffer_ms if keep_tail_ms is None else keep_tail_ms
        if tail > 0 and self.is_streaming:
            time.sleep(min(tail, 1000) / 1000.0)
        with self._lock:
            self._capturing = False
            data = (
                np.concatenate(self._captured).astype(np.float32, copy=False)
                if self._captured
                else np.zeros(0, dtype=np.float32)
            )
            self._captured = []
            self._captured_samples = 0
        return self._postprocess(data)

    def cancel(self) -> None:
        with self._lock:
            self._capturing = False
            self._captured = []
            self._captured_samples = 0

    def _postprocess(self, data: np.ndarray) -> np.ndarray:
        if data.size == 0:
            return data
        if self.settings.dc_offset_removal:
            data = data - float(np.mean(data))
        gain = float(self.settings.input_gain)
        if gain != 1.0:
            data = data * gain
        return np.clip(data, -1.0, 1.0).astype(np.float32)

    # -- PortAudio callback (realtime thread; keep it cheap) ---------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            if getattr(status, "input_overflow", False):
                self._overflows += 1
            elif status:
                log.debug("Audio status: %s", status)

        try:
            block = to_mono(indata)
            if self._stream_rate != TARGET_RATE:
                block = resample_to_16k(block, self._stream_rate)
            else:
                block = np.array(block, dtype=np.float32, copy=True)
        except Exception:  # pragma: no cover - never raise into PortAudio
            log.debug("Audio block conversion failed", exc_info=True)
            return

        if block.size == 0:
            return

        peak = float(np.max(np.abs(block)))
        rms = float(np.sqrt(np.mean(np.square(block))))
        # Smooth attack/decay so the meter reads like a VU meter, not a strobe.
        smoothing = 0.35 if rms > self._level else 0.12
        self._level += (rms - self._level) * smoothing
        self._peak = max(peak, self._peak * 0.92)
        if peak > self._peak_since_open:
            self._peak_since_open = peak

        pre_samples = int(TARGET_RATE * max(0, self.settings.pre_buffer_ms) / 1000)
        with self._lock:
            if self._capturing:
                self._captured.append(block)
                self._captured_samples += block.size
            self._ring.append(block)
            self._ring_samples += block.size
            while self._ring_samples > pre_samples and self._ring:
                self._ring_samples -= self._ring.popleft().size
            over_limit = (
                self._capturing
                and self.settings.max_duration_s > 0
                and self._captured_samples > self.settings.max_duration_s * TARGET_RATE
            )

        now = time.perf_counter()
        if self._on_level and now - self._last_level_emit > 0.045:
            self._last_level_emit = now
            try:
                self._on_level(self._level, self._peak)
            except Exception:
                log.debug("Level callback failed", exc_info=True)

        if over_limit and not self._limit_fired:
            self._limit_fired = True
            if self._on_limit:
                threading.Thread(target=self._safe_limit, daemon=True).start()

    def _safe_limit(self) -> None:
        try:
            if self._on_limit:
                self._on_limit()
        except Exception:
            log.debug("Limit callback failed", exc_info=True)

    def _emit_error(self, code: str, message: str) -> None:
        if self._on_error:
            try:
                self._on_error(code, message)
            except Exception:
                log.debug("Error callback failed", exc_info=True)

    def health(self) -> dict:
        return {
            "streaming": self.is_streaming,
            "capturing": self._capturing,
            "device_id": self._device_id,
            "device_name": self._device_name,
            "device_host_api": self._device_host_api,
            "device_is_alias": self._device_is_alias,
            "silent_seconds": round(self.silent_seconds, 1),
            "stream_rate": self._stream_rate,
            "overflows": self._overflows,
            "level": round(self._level, 4),
        }


def _friendly(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if "invalid device" in lowered or "device unavailable" in lowered:
        return "That microphone is unavailable. Pick another input device in Settings > Audio."
    if "access" in lowered or "denied" in lowered:
        return (
            "Windows blocked microphone access. Enable it in "
            "Settings > Privacy & security > Microphone."
        )
    if "in use" in lowered or "busy" in lowered:
        return "Another app is using the microphone exclusively. Close it and try again."
    return "Could not open the microphone: " + text


__all__ = ["AudioCapture", "MicrophoneError", "AudioUnavailable"]
