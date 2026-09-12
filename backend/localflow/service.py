"""LocalFlowService - the backend's public surface.

Everything the desktop shell can ask for is a method here.  The transport
(:mod:`localflow.server`) does nothing but validate JSON and call into this
class, which keeps the interesting logic testable without a socket.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import paths
from .asr.engine import AsrError, WhisperEngine, build_initial_prompt
from .asr.gpu import probe
from .asr.models import catalog_payload, recommend
from .audio.capture import AudioCapture, MicrophoneError
from .audio.devices import AudioUnavailable, deduplicate_devices, list_input_devices
from .audio.devices import refresh as refresh_audio_devices
from .commands import registry as commands
from .config import Settings, settings_from_dict, settings_to_dict
from .context.apps import builtin_profiles
from .context.engine import ContextEngine, ContextSnapshot
from .database import (
    ApplicationProfileRepo,
    CorrectionRepo,
    Database,
    HistoryRepo,
    SettingsRepo,
    SnippetRepo,
    VocabularyRepo,
)
from .llm.provider import SUGGESTED_MODELS, LlmError, OllamaProvider, validate_model_name
from .metrics import QualityMetrics
from .processing import style as style_module
from .processing.pipeline import PipelineInput, ProcessingPipeline
from .session import DictationSession, SessionResult, SessionState
from .snippets.engine import SnippetEngine, starter_snippets
from .vad.segmenter import VadProcessor
from .vocabulary.engine import VocabularyEngine

log = logging.getLogger(__name__)

EmitFn = Callable[[str, dict], None]

STARTER_VOCABULARY = [
    ("LocalFlow", ["local flow", "locoflow"]),
    ("Ollama", ["olama", "oh llama", "ojama"]),
    ("Whisper", []),
    ("GSTIN", ["g s t i n", "gst in"]),
    ("ICEGATE", ["ice gate", "icegate"]),
]


class LocalFlowService:
    def __init__(self, data_dir: Path | None = None, emit: EmitFn | None = None):
        self.data_dir = data_dir or paths.DATA_DIR
        paths.DATA_DIR = self.data_dir
        paths.MODELS_DIR = self.data_dir / "models"
        paths.AUDIO_DIR = self.data_dir / "audio"
        paths.LOGS_DIR = self.data_dir / "logs"
        paths.RUNTIME_DIR = self.data_dir / "runtime"
        paths.DB_PATH = self.data_dir / "localflow.db"
        paths.ensure_dirs()

        self._emit_fn = emit
        self.db = Database(paths.DB_PATH)
        self.settings_repo = SettingsRepo(self.db)
        self.vocabulary_repo = VocabularyRepo(self.db)
        self.correction_repo = CorrectionRepo(self.db)
        self.snippet_repo = SnippetRepo(self.db)
        self.history_repo = HistoryRepo(self.db)
        self.profile_repo = ApplicationProfileRepo(self.db)
        self.quality = QualityMetrics(self.db)

        self.settings: Settings = settings_from_dict(self.settings_repo.load_all())
        self._seed_defaults()

        self.vocabulary = VocabularyEngine(self.settings.processing.vocabulary_fuzzy_threshold)
        self.snippets = SnippetEngine()
        self.context_engine = ContextEngine()
        self.llm = OllamaProvider(self.settings.llm.base_url, self.settings.llm.timeout_s)
        self.pipeline = ProcessingPipeline(
            self.vocabulary, self.snippets, self.context_engine, self.llm
        )

        self.vad = VadProcessor(self.settings.vad, paths.MODELS_DIR)
        self.asr = WhisperEngine(self.settings.asr, paths.MODELS_DIR)
        self.capture = AudioCapture(
            self.settings.audio,
            on_level=self._on_level,
            on_error=self._on_audio_error,
            on_limit=self._on_duration_limit,
        )

        self.session: DictationSession | None = None
        self._session_context: dict | None = None
        self._session_lock = threading.RLock()
        self.last_result: SessionResult | None = None
        self.last_history_id: int | None = None
        self.paused = False
        self.ready_error = ""
        self._level_seq = 0
        self._pull_lock = threading.RLock()
        self._pull_cancel = threading.Event()
        self._pull_active = ""
        self._reload_user_data()

    # -- wiring ------------------------------------------------------------
    def set_emitter(self, emit: EmitFn) -> None:
        self._emit_fn = emit

    def emit(self, event: str, data: dict) -> None:
        if self._emit_fn is None:
            return
        try:
            self._emit_fn(event, data)
        except Exception:
            log.debug("Emit failed for %s", event, exc_info=True)

    def _seed_defaults(self) -> None:
        if not self.settings_repo.load_all():
            self.settings_repo.save_all(settings_to_dict(self.settings))
        if self.profile_repo.count() == 0:
            for profile in builtin_profiles():
                self.profile_repo.upsert(profile)
        if not self.vocabulary_repo.list(limit=1):
            for term, aliases in STARTER_VOCABULARY:
                self.vocabulary_repo.add(term, aliases, category="builtin")
        if not self.snippet_repo.list():
            for snippet in starter_snippets():
                self.snippet_repo.add(**snippet)

    def _reload_user_data(self) -> None:
        self.vocabulary.load(
            self.vocabulary_repo.active(),
            self.correction_repo.active() if self.settings.processing.learning_enabled else [],
        )
        self.snippets.load(self.snippet_repo.active())
        self.context_engine.configure(
            self.profile_repo.active(),
            self.settings.processing.default_style,
            self.settings.processing.use_context,
        )
        self.pipeline.configure(self.settings.processing, self.settings.llm)

    # -- startup -----------------------------------------------------------
    def start(self, warm: bool = True) -> dict:
        """Bring the engines up.  Failures are reported, never fatal."""
        status: dict[str, Any] = {"asr": "pending", "vad": "pending", "audio": "pending"}
        try:
            self.vad.load()
            status["vad"] = self.vad.backend
        except Exception as exc:
            status["vad"] = "error"
            log.warning("VAD load failed: %s", exc)

        try:
            self.capture.open()
            status["audio"] = "ready"
        except MicrophoneError as exc:
            status["audio"] = "error"
            self.emit("error", {"code": exc.code, "message": str(exc), "fatal": False})
        except Exception as exc:
            status["audio"] = "error"
            log.warning("Audio open failed: %s", exc)

        if not self.settings.llm.model:
            hardware = probe()
            picked = self.llm.recommend(hardware.get("vram_free_mb", 0))
            if picked:
                self.settings.llm.model = picked
                self._persist_section("llm")

        if warm:
            threading.Thread(target=self._warm_async, name="warmup", daemon=True).start()
            status["asr"] = "loading"
        return status

    def _warm_async(self) -> None:
        try:
            self.asr.ensure_loaded(progress=lambda state, message: self.emit(
                "asr.status", {"state": state, "message": message, **self.asr.info()}
            ))
            self.emit("asr.status", {"state": "ready", "message": "Speech model ready",
                                     **self.asr.info()})
        except AsrError as exc:
            self.ready_error = str(exc)
            self.emit("error", {"code": exc.code, "message": str(exc), "detail": exc.detail,
                                "fatal": False})
        except Exception as exc:
            self.ready_error = str(exc)
            log.exception("Unexpected ASR warm-up failure")
            self.emit("error", {"code": "asr_failed", "message": str(exc), "fatal": False})

        if self.settings.llm.enabled and self.settings.llm.model:
            ok, _ = self.llm.available()
            if ok:
                self.llm.warm(self.settings.llm.model)
                self.emit("llm.status", self.llm_status())

    def shutdown(self) -> None:
        with self._session_lock:
            if self.session is not None:
                self.session.cancel()
                self.session = None
        self.capture.close()
        self.asr.unload()
        self.llm.close()
        self.db.close()

    # -- audio callbacks ---------------------------------------------------
    def _on_level(self, level: float, peak: float) -> None:
        self._level_seq += 1
        self.emit("audio.level", {"level": round(level, 4), "peak": round(peak, 4),
                                  "seq": self._level_seq})

    def _on_audio_error(self, code: str, message: str) -> None:
        self.emit("error", {"code": code, "message": message, "fatal": False})

    def _on_duration_limit(self) -> None:
        self.emit("session.limit", {"max_seconds": self.settings.audio.max_duration_s})

    # -- settings ----------------------------------------------------------
    def get_settings(self) -> dict:
        return settings_to_dict(self.settings)

    def _persist_section(self, section: str) -> None:
        payload = settings_to_dict(self.settings).get(section, {})
        self.settings_repo.save_section(section, payload)

    def update_settings(self, patch: dict) -> dict:
        """Apply a partial settings update and reconfigure affected engines."""
        current = settings_to_dict(self.settings)
        for section, values in (patch or {}).items():
            if section in current and isinstance(values, dict):
                current[section].update(values)
        new_settings = settings_from_dict(current)
        touched = set((patch or {}).keys())

        audio_error: MicrophoneError | None = None
        if "audio" in touched:
            try:
                self.capture.reconfigure(new_settings.audio)
            except MicrophoneError as exc:
                # Keep the user's choice and tell them it did not take effect.
                # Rolling the whole settings update back would lose unrelated
                # changes made in the same save.
                audio_error = exc
        if "vad" in touched:
            self.vad.reconfigure(new_settings.vad)
        if "asr" in touched:
            needs_reload = self.asr.reconfigure(new_settings.asr)
            if needs_reload:
                threading.Thread(target=self._warm_async, name="reload", daemon=True).start()
        if "llm" in touched:
            self.llm.configure(new_settings.llm.base_url, new_settings.llm.timeout_s)

        self.settings = new_settings
        self.settings_repo.save_all(settings_to_dict(self.settings))
        self._reload_user_data()
        self.emit("settings.changed", {"sections": sorted(touched)})
        if audio_error is not None:
            self.emit(
                "error",
                {"code": audio_error.code, "message": str(audio_error), "fatal": False},
            )
        return settings_to_dict(self.settings)

    def reset_settings(self) -> dict:
        self.settings_repo.reset()
        self.settings = Settings()
        self.settings_repo.save_all(settings_to_dict(self.settings))
        self._reload_user_data()
        return settings_to_dict(self.settings)

    # -- hardware and models ----------------------------------------------
    def hardware(self, refresh: bool = True) -> dict:
        info = self.asr.hardware(refresh=refresh)
        recommendation = recommend(info)
        return {**info, "recommendation": recommendation}

    def audio_devices(self, all_devices: bool = False) -> dict:
        """Input devices for the picker.

        Collapsed to one entry per physical microphone by default - Windows
        lists each one once per host API, which makes a two-microphone laptop
        look like it has fifteen.
        """
        try:
            raw = list_input_devices()
        except AudioUnavailable as exc:
            return {"devices": [], "total": 0, "error": str(exc)}
        devices = raw if all_devices else deduplicate_devices(raw)
        # Which device is *actually* open matters more than which one is
        # configured: the stored setting may say "system default" while the
        # stream is on a specific endpoint, and a fallback may have moved it.
        # The name is read back from the capture object rather than looked up
        # by index, because indices are renumbered by any plug or unplug and
        # reporting the wrong device is how a dead meter goes unexplained.
        return {
            "devices": devices,
            "total": len(raw),
            "error": "",
            "active_id": self.capture.device_id,
            "active_name": self.capture.device_name,
            "active_host_api": self.capture.device_host_api,
            "active_is_alias": self.capture.device_is_alias,
            "silent_seconds": round(self.capture.silent_seconds, 1),
            "streaming": self.capture.is_streaming,
        }

    def rescan_audio(self, all_devices: bool = False) -> dict:
        """Re-enumerate microphones and reopen the stream.

        PortAudio caches the device list when it initialises, so a headset
        plugged in after launch is invisible - and the cached indices can point
        at endpoints Windows has renumbered, which makes opening a device that
        plainly exists fail. This is the escape hatch for that, and the reason
        the stream is closed first: re-initialising PortAudio underneath a live
        stream faults the process.
        """
        was_streaming = self.capture.is_streaming
        self.capture.close()
        refresh_audio_devices()
        error = ""
        if was_streaming:
            try:
                self.capture.open()
            except MicrophoneError as exc:
                error = str(exc)
                log.warning("Reopening the microphone after a rescan failed: %s", exc)
        result = self.audio_devices(all_devices)
        if error:
            result["error"] = error
        return result

    def asr_models(self) -> dict:
        return {
            "catalog": catalog_payload(self.asr.hardware()),
            "current": self.asr.info(),
            "downloaded": self._downloaded_models(),
        }

    def _downloaded_models(self) -> list[str]:
        root = paths.MODELS_DIR / "whisper"
        if not root.is_dir():
            return []
        found: list[str] = []
        for entry in root.glob("models--*"):
            name = entry.name.replace("models--", "").replace("--", "/")
            found.append(name)
        return found

    def asr_load(self) -> dict:
        self.asr.ensure_loaded(progress=lambda state, message: self.emit(
            "asr.status", {"state": state, "message": message}
        ))
        return self.asr.info()

    def asr_unload(self) -> dict:
        self.asr.unload()
        self.emit("asr.status", {"state": "unloaded", **self.asr.info()})
        return self.asr.info()

    def asr_warm(self) -> dict:
        self.asr.ensure_loaded()
        seconds = self.asr.warm_up()
        return {**self.asr.info(), "warm_seconds": round(seconds, 3)}

    def llm_status(self) -> dict:
        ok, message = self.llm.available()
        models: list[dict] = []
        if ok:
            try:
                models = [asdict(m) | {"size_gb": m.size_gb} for m in self.llm.list_models()]
            except LlmError as exc:
                ok, message = False, str(exc)
        return {
            "available": ok,
            "message": message,
            "provider": self.llm.name,
            "base_url": self.llm.base_url,
            "model": self.settings.llm.model,
            "enabled": self.settings.llm.enabled,
            "models": models,
            "running": self.llm.running() if ok else [],
            "suggested": list(SUGGESTED_MODELS),
            "downloading": self._pull_active,
        }

    def llm_test(self, model: str = "") -> dict:
        target = model or self.settings.llm.model
        if not target:
            return {"ok": False, "message": "No model selected."}
        started = time.perf_counter()
        try:
            system, user = self.pipeline.prompts.build_command(
                "Fix capitalisation and punctuation only.", "hello this is a test of local flow"
            )
            result = self.llm.generate(
                system, user, model=target, timeout=max(20.0, self.settings.llm.timeout_s),
                keep_alive=self.settings.llm.keep_alive, num_ctx=1024, temperature=0.0,
            )
        except LlmError as exc:
            return {"ok": False, "message": str(exc), "detail": exc.detail, "code": exc.code}
        return {
            "ok": True,
            "message": "Model responded.",
            "output": result.text[:200],
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "model": target,
        }

    def llm_warm(self, model: str = "") -> dict:
        target = model or self.settings.llm.model
        return {"ok": self.llm.warm(target), "model": target}

    def llm_unload(self, model: str = "") -> dict:
        target = model or self.settings.llm.model
        return {"ok": self.llm.unload(target), "model": target}

    def llm_show(self, model: str) -> dict:
        return self.llm.show(model)

    def llm_pull(self, model: str, select: bool = True) -> dict:
        """Download any model from the Ollama registry.

        Deliberately unrestricted: the user can name anything Ollama accepts,
        including a Hugging Face GGUF repo, not just what is already installed.
        """
        name = validate_model_name(model)
        with self._pull_lock:
            if self._pull_active:
                raise LlmError(
                    "llm_pull_busy",
                    f"Already downloading '{self._pull_active}'. Wait for it to finish "
                    "or cancel it first.",
                )
            self._pull_active = name
            self._pull_cancel.clear()

        started = time.time()
        last_emit = 0.0

        def on_progress(progress) -> None:
            nonlocal last_emit
            now = time.time()
            # Throttle: a big pull emits thousands of chunks.
            if now - last_emit < 0.2 and not progress.done:
                return
            last_emit = now
            self.emit(
                "llm.pull",
                {
                    "model": name,
                    "status": progress.status,
                    "completed": progress.completed,
                    "total": progress.total,
                    "percent": progress.percent,
                    "done": progress.done,
                },
            )

        try:
            result = self.llm.pull(
                name, on_progress=on_progress, cancel=self._pull_cancel.is_set
            )
        except LlmError as exc:
            self.emit("llm.pull", {"model": name, "status": "error", "error": str(exc),
                                   "code": exc.code, "done": True})
            raise
        finally:
            with self._pull_lock:
                self._pull_active = ""
                self._pull_cancel.clear()

        if select:
            self.settings.llm.model = name
            self._persist_section("llm")
            threading.Thread(
                target=self.llm.warm, args=(name,), name="warm-pulled", daemon=True
            ).start()
        self.emit("llm.status", self.llm_status())
        return {
            "ok": True,
            "model": name,
            "selected": select,
            "bytes": result.total,
            "seconds": round(time.time() - started, 1),
        }

    def llm_pull_cancel(self) -> dict:
        with self._pull_lock:
            active = self._pull_active
            if active:
                self._pull_cancel.set()
        return {"cancelled": bool(active), "model": active}

    def llm_delete(self, model: str) -> dict:
        name = validate_model_name(model)
        deleted = self.llm.delete(name)
        if deleted and self.settings.llm.model == name:
            hardware = probe()
            self.settings.llm.model = self.llm.recommend(hardware.get("vram_free_mb", 0))
            self._persist_section("llm")
        self.emit("llm.status", self.llm_status())
        return {"ok": deleted, "model": name, "selected": self.settings.llm.model}

    # -- dictation ---------------------------------------------------------
    def session_start(
        self,
        context: dict | None = None,
        style: str = "",
        mode: str = "",
        silence_ms: int = 0,
    ) -> dict:
        with self._session_lock:
            if self.paused:
                raise RuntimeError("LocalFlow is paused.")
            if self.session is not None:
                self.session.cancel()
                self.session = None

            if not self.asr.ready and not self.asr.loading:
                threading.Thread(target=self._warm_async, name="lazyload", daemon=True).start()

            prompt = ""
            if self.settings.asr.use_vocabulary_prompt:
                prompt = build_initial_prompt(self.vocabulary.prompt_terms(limit=48))

            session_id = uuid.uuid4().hex[:12]
            language = None if self.settings.asr.language == "auto" else self.settings.asr.language
            session = DictationSession(
                session_id=session_id,
                capture=self.capture,
                vad=self.vad,
                asr=self.asr,
                emit=self.emit,
                partial_interval_ms=self.settings.asr.partial_interval_ms,
                partial_min_audio_ms=self.settings.asr.partial_min_audio_ms,
                streaming=(
                    self.settings.asr.streaming_partials and self.settings.appearance.show_partials
                ),
                initial_prompt=prompt,
                language=language,
                auto_stop_silence_ms=(
                    silence_ms or self.settings.hotkeys.hands_free_silence_ms
                    if (mode or self.settings.hotkeys.mode) == "hands_free"
                    else 0
                ),
            )
            self._session_context = dict(context or {})
            self._session_context["_style"] = style
            try:
                session.start()
            except MicrophoneError as exc:
                self.emit("error", {"code": exc.code, "message": str(exc), "fatal": False})
                raise
            self.session = session
            return {"session_id": session_id, "state": SessionState.LISTENING.value}

    def session_cancel(self) -> dict:
        with self._session_lock:
            if self.session is None:
                return {"cancelled": False}
            self.session.cancel()
            self.session = None
            self._session_context = None
        self.emit("session.state", {"state": SessionState.IDLE.value})
        return {"cancelled": True}

    def session_stop(self, context: dict | None = None) -> dict:
        with self._session_lock:
            session = self.session
            self.session = None
            stored_context = self._session_context or {}
            self._session_context = None
        if session is None:
            raise RuntimeError("No dictation is in progress.")

        merged = {**stored_context, **(context or {})}
        style_override = merged.pop("_style", "") or ""
        total_started = session.timings.started_at

        audio = session.stop_recording()
        session.join_partials(timeout=0.8)
        if session.cancelled:
            return asdict(SessionResult(session_id=session.id, cancelled=True))

        try:
            raw_text, meta = session.transcribe(audio)
        except AsrError as exc:
            self.emit("error", {"code": exc.code, "message": str(exc), "detail": exc.detail,
                                "fatal": False})
            raise

        snapshot = ContextSnapshot.from_dict(merged)
        resolved = self.context_engine.resolve(
            snapshot,
            style_override=style_override,
            llm_globally_enabled=self.settings.llm.enabled,
        )

        if not raw_text.strip():
            session.timings.total_ms = (time.perf_counter() - total_started) * 1000.0
            result = SessionResult(
                session_id=session.id,
                had_speech=bool(meta.get("had_speech")),
                timings=session.timings.as_dict(),
                diagnostics=meta,
            )
            self.emit("session.state", {"state": SessionState.IDLE.value})
            self.last_result = result
            return asdict(result)

        process_started = time.perf_counter()
        pipeline_result = self.pipeline.run(
            PipelineInput(
                raw_text=raw_text,
                context=resolved,
                segments=meta.get("segments", []),
                language=meta.get("language", ""),
                avg_logprob=float(meta.get("avg_logprob", 0.0) or 0.0),
                duration_s=float(meta.get("audio_seconds", 0.0) or 0.0),
                style_override=style_override,
            )
        )
        session.timings.process_ms = (time.perf_counter() - process_started) * 1000.0
        session.timings.llm_ms = pipeline_result.timings.get("llm", 0.0)
        session.timings.total_ms = (time.perf_counter() - total_started) * 1000.0

        audio_path = self._maybe_store_audio(session.id, audio)
        result = SessionResult(
            session_id=session.id,
            text=pipeline_result.text,
            insert_text=pipeline_result.insert_text,
            raw_transcript=raw_text,
            deterministic_text=pipeline_result.deterministic_text,
            language=meta.get("language", ""),
            used_llm=pipeline_result.used_llm,
            llm_model=pipeline_result.llm_model,
            command=asdict(pipeline_result.command) if pipeline_result.command else None,
            snippet=(
                {"name": pipeline_result.snippet.name, "trigger": pipeline_result.snippet.trigger}
                if pipeline_result.snippet
                else None
            ),
            replace_selection=resolved.replace_selection,
            timings=session.timings.as_dict(),
            warnings=pipeline_result.warnings,
            had_speech=True,
            diagnostics={
                **meta,
                "context": resolved.as_dict(),
                "stages": pipeline_result.timings,
                "llm_reason": pipeline_result.llm_reason or pipeline_result.llm_skipped_reason,
                "corrections": pipeline_result.corrections,
                "removed_fillers": pipeline_result.removed_fillers,
                "vocabulary_hits": pipeline_result.vocabulary_hits,
                "validation": (
                    {"ok": pipeline_result.validation.ok,
                     "reason": pipeline_result.validation.reason}
                    if pipeline_result.validation
                    else None
                ),
            },
        )

        if pipeline_result.command is None and pipeline_result.text:
            self.last_history_id = self._record_history(result, resolved, audio_path)
        self.last_result = result
        if pipeline_result.vocabulary_hits:
            self.vocabulary_repo.bump_hits(pipeline_result.vocabulary_hits)
        if pipeline_result.snippet is not None:
            self.snippet_repo.bump_use(pipeline_result.snippet.trigger)
        return asdict(result)

    def _maybe_store_audio(self, session_id: str, audio: np.ndarray) -> str | None:
        if not self.settings.privacy.store_audio or audio.size == 0:
            return None
        try:
            import soundfile as sf

            paths.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            target = paths.AUDIO_DIR / f"{session_id}.wav"
            sf.write(str(target), audio, 16000, subtype="PCM_16")
            return str(target)
        except Exception:
            log.debug("Audio history write failed", exc_info=True)
            return None

    def _record_history(self, result: SessionResult, resolved, audio_path: str | None) -> int:
        if not self.settings.privacy.store_history:
            return 0
        entry = {
            "app_exe": resolved.snapshot.exe,
            "app_name": resolved.identity.app_name,
            "app_category": resolved.identity.category,
            "window_title": resolved.snapshot.window_title[:200],
            "raw_transcript": result.raw_transcript,
            "final_text": result.text,
            "language": result.language,
            "style": resolved.style,
            "duration_ms": int(result.timings.get("record_ms", 0)),
            "used_llm": result.used_llm,
            "latency": result.timings,
            "audio_path": audio_path,
            "inserted": False,
            "word_count": len(result.text.split()),
        }
        history_id = self.history_repo.add(entry)
        self._prune_history()
        self.emit("history.added", {"id": history_id})
        return history_id

    def mark_inserted(self, history_id: int | None = None, inserted: bool = True) -> dict:
        target = history_id or self.last_history_id
        if not target:
            return {"ok": False}
        self.db.execute("UPDATE history SET inserted=? WHERE id=?", (int(inserted), target))
        return {"ok": True, "id": target}

    def _prune_history(self) -> None:
        removed = self.history_repo.prune(
            self.settings.privacy.history_retention_days,
            self.settings.privacy.audio_retention_days,
        )
        for path in removed:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass

    # -- text-only processing (tests, benchmarks, re-runs) -----------------
    def process_text(
        self,
        text: str,
        context: dict | None = None,
        style: str = "",
        segments: list[dict] | None = None,
        force_llm: bool | None = None,
    ) -> dict:
        snapshot = ContextSnapshot.from_dict(context or {})
        resolved = self.context_engine.resolve(
            snapshot, style_override=style, llm_globally_enabled=self.settings.llm.enabled
        )
        previous = self.pipeline.llm_settings.always_use
        if force_llm is not None:
            self.pipeline.llm_settings.always_use = bool(force_llm)
            if force_llm is False:
                self.pipeline.llm = None
        try:
            result = self.pipeline.run(
                PipelineInput(
                    raw_text=text,
                    context=resolved,
                    segments=segments or [],
                    style_override=style,
                )
            )
        finally:
            self.pipeline.llm_settings.always_use = previous
            self.pipeline.llm = self.llm
        return {
            "text": result.text,
            "insert_text": result.insert_text,
            "deterministic_text": result.deterministic_text,
            "used_llm": result.used_llm,
            "llm_reason": result.llm_reason or result.llm_skipped_reason,
            "command": asdict(result.command) if result.command else None,
            "snippet": (
                {"name": result.snippet.name, "trigger": result.snippet.trigger}
                if result.snippet else None
            ),
            "corrections": result.corrections,
            "removed_fillers": result.removed_fillers,
            "vocabulary_hits": result.vocabulary_hits,
            "timings": result.timings,
            "warnings": result.warnings,
            "context": resolved.as_dict(),
            "validation": (
                {"ok": result.validation.ok, "reason": result.validation.reason}
                if result.validation else None
            ),
        }

    def run_text_command(self, instruction: str, text: str) -> dict:
        return {"text": self.pipeline.run_command(instruction, text)}

    # -- vocabulary --------------------------------------------------------
    def vocabulary_list(self, search: str = "") -> dict:
        return {"items": self.vocabulary_repo.list(search)}

    def vocabulary_add(self, term: str, sounds_like: list[str] | None = None,
                       category: str = "general", case_sensitive: bool = True) -> dict:
        item = self.vocabulary_repo.add(term, sounds_like, category, case_sensitive)
        self._reload_user_data()
        return {"item": item}

    def vocabulary_update(self, id: int, **fields: Any) -> dict:
        item = self.vocabulary_repo.update(id, **fields)
        self._reload_user_data()
        return {"item": item}

    def vocabulary_delete(self, id: int) -> dict:
        self.vocabulary_repo.delete(id)
        self._reload_user_data()
        return {"ok": True}

    def vocabulary_export(self) -> dict:
        return {"items": [
            {"term": i["term"], "sounds_like": i["sounds_like"], "category": i["category"]}
            for i in self.vocabulary_repo.list()
        ]}

    def vocabulary_import(self, items: list[dict], replace: bool = False) -> dict:
        if replace:
            self.vocabulary_repo.clear()
        added = 0
        for item in items or []:
            term = (item.get("term") or "").strip()
            if not term:
                continue
            self.vocabulary_repo.add(
                term, item.get("sounds_like") or [], item.get("category", "imported")
            )
            added += 1
        self._reload_user_data()
        return {"added": added}

    # -- learned corrections ----------------------------------------------
    def corrections_list(self) -> dict:
        return {"items": self.correction_repo.list()}

    def corrections_observe(self, original: str, edited: str) -> dict:
        """Learn from a user edit of text LocalFlow produced."""
        if not self.settings.processing.learning_enabled:
            return {"learned": [], "enabled": False}
        pairs = VocabularyEngine.diff_words(original, edited)
        learned = []
        for wrong, correct in pairs:
            entry = self.correction_repo.observe(
                wrong, correct, self.settings.processing.learning_threshold
            )
            if entry:
                learned.append(entry)
        if learned:
            self._reload_user_data()
        return {"learned": learned, "enabled": True}

    def corrections_delete(self, id: int) -> dict:
        self.correction_repo.delete(id)
        self._reload_user_data()
        return {"ok": True}

    def corrections_toggle(self, id: int, enabled: bool) -> dict:
        self.correction_repo.set_enabled(id, enabled)
        self._reload_user_data()
        return {"ok": True}

    def corrections_clear(self) -> dict:
        self.correction_repo.clear()
        self._reload_user_data()
        return {"ok": True}

    # -- snippets ----------------------------------------------------------
    def snippets_list(self) -> dict:
        return {"items": self.snippet_repo.list()}

    def snippets_add(self, name: str, trigger: str, expansion: str,
                     mode: str = "replace_all") -> dict:
        item = self.snippet_repo.add(name, trigger, expansion, mode)
        self._reload_user_data()
        return {"item": item}

    def snippets_update(self, id: int, **fields: Any) -> dict:
        item = self.snippet_repo.update(id, **fields)
        self._reload_user_data()
        return {"item": item}

    def snippets_delete(self, id: int) -> dict:
        self.snippet_repo.delete(id)
        self._reload_user_data()
        return {"ok": True}

    # -- history -----------------------------------------------------------
    def history_list(self, search: str = "", limit: int = 100, offset: int = 0) -> dict:
        return {"items": self.history_repo.list(search, limit, offset),
                "stats": self.history_repo.stats()}

    def history_get(self, id: int) -> dict:
        return {"item": self.history_repo.get(id)}

    def history_delete(self, id: int) -> dict:
        audio = self.history_repo.delete(id)
        if audio:
            Path(audio).unlink(missing_ok=True)
        return {"ok": True}

    def history_clear(self) -> dict:
        for audio in self.history_repo.clear():
            Path(audio).unlink(missing_ok=True)
        return {"ok": True}

    def history_edit(self, id: int, text: str) -> dict:
        entry = self.history_repo.get(id)
        if entry:
            self.corrections_observe(entry["final_text"], text)
            self.history_repo.mark_edited(id, text)
            if text.strip() != (entry["final_text"] or "").strip():
                self.quality.mark_edited(id, reason="edit")
        return {"ok": bool(entry)}

    def history_stats(self) -> dict:
        return self.history_repo.stats()

    # -- quality metrics ---------------------------------------------------
    def quality_snapshot(self, days: int = 30) -> dict:
        """Zero-edit rate and latency percentiles from real usage."""
        return self.quality.snapshot(days).as_dict()

    def record_undo(self, history_id: int | None = None) -> dict:
        """Attribute an undo to the dictation it removed.

        Called by the desktop shell whenever the user undoes an insertion,
        whether by shortcut or by saying "undo that" - both mean LocalFlow got
        it wrong, which is exactly what the zero-edit rate must capture.
        """
        target = history_id or self.last_history_id
        if target:
            self.quality.mark_undone(int(target))
            return {"ok": True, "id": target}
        attributed = self.quality.undo_recent()
        return {"ok": attributed is not None, "id": attributed}

    # -- profiles ----------------------------------------------------------
    def profiles_list(self) -> dict:
        return {"items": self.profile_repo.list()}

    def profiles_upsert(self, **profile: Any) -> dict:
        item = self.profile_repo.upsert(profile)
        self._reload_user_data()
        return {"item": item}

    def profiles_delete(self, id: int) -> dict:
        self.profile_repo.delete(id)
        self._reload_user_data()
        return {"ok": True}

    # -- misc --------------------------------------------------------------
    def set_paused(self, paused: bool) -> dict:
        self.paused = bool(paused)
        if self.paused:
            self.session_cancel()
        self.emit("state.paused", {"paused": self.paused})
        return {"paused": self.paused}

    def catalogues(self) -> dict:
        return {
            "styles": style_module.payload(),
            "commands": commands.describe(),
            "categories": list(
                {p["category"] for p in self.profile_repo.list()} | {"general"}
            ),
        }

    def diagnostics(self) -> dict:
        hardware = self.asr.hardware(refresh=True)
        last = self.last_result
        return {
            "version": __import__("localflow").__version__,
            "hardware": hardware,
            "asr": self.asr.info(),
            "vad": self.vad.info(),
            "audio": self.capture.health(),
            "llm": {
                "model": self.settings.llm.model,
                "enabled": self.settings.llm.enabled,
                "available": self.llm.available()[0],
                "running": self.llm.running(),
            },
            "vocabulary_terms": self.vocabulary.size,
            "snippets": self.snippets.size,
            "paused": self.paused,
            "data_dir": str(self.data_dir),
            "last": {
                "raw": last.raw_transcript if last else "",
                "deterministic": last.deterministic_text if last else "",
                "final": last.text if last else "",
                "timings": last.timings if last else {},
                "diagnostics": last.diagnostics if last else {},
            },
            "history": self.history_repo.stats(),
            "ready_error": self.ready_error,
        }

    def privacy_report(self) -> dict:
        """Exactly what leaves this machine: nothing, and why."""
        return {
            "audio_processing": "Local (in-memory)",
            "speech_recognition": f"Local (faster-whisper, {self.asr.loaded_device or 'pending'})",
            "ai_processing": (
                f"Local (Ollama at {self.llm.base_url})"
                if self.settings.llm.enabled
                else "Disabled"
            ),
            "history": "Local SQLite" if self.settings.privacy.store_history else "Not stored",
            "audio_history": (
                f"Stored locally in {paths.AUDIO_DIR}"
                if self.settings.privacy.store_audio
                else "Not stored"
            ),
            "telemetry": "Disabled" if not self.settings.privacy.telemetry else "Enabled",
            "network": [
                {
                    "name": "Ollama",
                    "endpoint": self.llm.base_url,
                    "scope": "Loopback only. Never leaves this machine.",
                    "required": self.settings.llm.enabled,
                },
                {
                    "name": "Model download (one time)",
                    "endpoint": "huggingface.co",
                    "scope": "Only when a speech model has not been downloaded yet.",
                    "required": False,
                },
            ],
            "data_dir": str(self.data_dir),
        }

    def export_all(self) -> dict:
        return {
            "settings": settings_to_dict(self.settings),
            "vocabulary": self.vocabulary_repo.list(),
            "snippets": self.snippet_repo.list(),
            "corrections": self.correction_repo.list(),
            "profiles": [p for p in self.profile_repo.list() if not p["builtin"]],
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def import_all(self, payload: dict) -> dict:
        counts = {"vocabulary": 0, "snippets": 0, "profiles": 0}
        if isinstance(payload.get("settings"), dict):
            self.update_settings(payload["settings"])
        for item in payload.get("vocabulary") or []:
            if self.vocabulary_repo.add(
                item.get("term", ""), item.get("sounds_like") or [],
                item.get("category", "imported")
            ):
                counts["vocabulary"] += 1
        for item in payload.get("snippets") or []:
            if self.snippet_repo.add(
                item.get("name", ""), item.get("trigger", ""), item.get("expansion", ""),
                item.get("mode", "replace_all")
            ):
                counts["snippets"] += 1
        for item in payload.get("profiles") or []:
            if self.profile_repo.upsert(item):
                counts["profiles"] += 1
        self._reload_user_data()
        return counts

    def wipe_all_data(self) -> dict:
        """Delete every trace of dictation from this machine."""
        for audio in self.history_repo.clear():
            Path(audio).unlink(missing_ok=True)
        self.correction_repo.clear()
        for path in paths.AUDIO_DIR.glob("*.wav"):
            path.unlink(missing_ok=True)
        self.db.vacuum()
        return {"ok": True}
