"""Typed settings model.

Settings are stored as one JSON blob per section in the ``settings`` SQLite
table (see :mod:`localflow.database`).  Keeping a typed in-memory model means
the rest of the code never has to guess at key names or defaults, while the
JSON-per-section storage means adding a field never needs a migration.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AudioSettings:
    device_id: int | None = None          # None -> system default input
    device_name: str = ""
    sample_rate: int = 16000
    channels: int = 1
    blocksize: int = 512                  # 32 ms at 16 kHz
    pre_buffer_ms: int = 500              # audio kept from *before* the hotkey
    post_buffer_ms: int = 350             # trailing audio kept after release
    max_duration_s: int = 300
    input_gain: float = 1.0
    dc_offset_removal: bool = True


@dataclass
class VadSettings:
    enabled: bool = True
    threshold: float = 0.45
    min_speech_ms: int = 120
    min_silence_ms: int = 450
    speech_pad_ms: int = 250              # never clip first/last syllable
    max_trim_lead_ms: int = 3000          # refuse to trim more than this


@dataclass
class AsrSettings:
    model: str = "small"
    device: str = "auto"                  # auto | cuda | cpu
    compute_type: str = "auto"            # auto | float16 | int8_float16 | int8
    language: str = "auto"
    task: str = "transcribe"              # transcribe | translate
    beam_size: int = 5
    partial_beam_size: int = 1
    vad_filter: bool = False              # we run our own Silero pass
    streaming_partials: bool = True
    partial_interval_ms: int = 750
    partial_min_audio_ms: int = 900
    condition_on_previous_text: bool = False
    temperature: float = 0.0
    use_vocabulary_prompt: bool = True
    cpu_threads: int = 0                  # 0 -> CTranslate2 default
    keep_warm: bool = True
    unload_after_idle_s: int = 0          # 0 -> never unload


@dataclass
class LlmSettings:
    enabled: bool = True
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = ""                       # "" -> auto-select on first use
    timeout_s: float = 15.0
    # How long Ollama keeps the model in VRAM after a call.
    #
    # Deliberately short. The speech model is resident for every dictation and
    # the language model runs on roughly one in eight, so on a 6 GB card a long
    # keep-alive means the *editor* squats on VRAM that the *transcriber* needs.
    # Measured on an RTX 3060 6 GB with large-v3-turbo resident: with a 3B model
    # also held on the GPU the card sits at 94% and Windows starts paging GPU
    # memory over PCIe - a one-second clip went from 0.4 s to 345 s. Releasing
    # the editor promptly costs a reload on back-to-back cleanups; keeping it
    # costs every dictation for the next quarter of an hour.
    keep_alive: str = "30s"
    num_ctx: int = 4096
    temperature: float = 0.0
    top_p: float = 0.9
    always_use: bool = False
    max_input_chars: int = 6000
    max_output_ratio: float = 2.2         # reject runaway generations


@dataclass
class ProcessingSettings:
    remove_fillers: bool = True
    filler_aggressiveness: str = "balanced"   # conservative | balanced | aggressive
    resolve_corrections: bool = True
    auto_punctuation: bool = True
    auto_paragraphs: bool = True
    auto_lists: bool = True
    auto_quotes: bool = True
    auto_email_format: bool = True
    smart_capitalization: bool = True
    default_style: str = "neutral"
    use_context: bool = True
    commands_enabled: bool = True
    snippets_enabled: bool = True
    learning_enabled: bool = True
    learning_threshold: int = 3           # repeats before a correction is learned
    vocabulary_enabled: bool = True
    vocabulary_fuzzy_threshold: int = 86  # rapidfuzz score 0-100


@dataclass
class HotkeySettings:
    primary: str = "Ctrl+Space"
    mode: str = "push_to_talk"            # push_to_talk | toggle | hands_free
    cancel_key: str = "Escape"
    tap_toggle_ms: int = 220
    hands_free_silence_ms: int = 1800
    undo_hotkey: str = "Ctrl+Shift+Z"
    enabled: bool = True


@dataclass
class InjectionSettings:
    method: str = "auto"                  # auto | unicode | clipboard
    # Length above which text is pasted rather than typed. 0 means always
    # paste: synthesising keystrokes races the target application's input
    # queue and drops characters, and any modifier the user is still holding
    # turns those keystrokes into shortcuts. Terminals, which cannot take a
    # Ctrl+V, pin themselves to 'unicode' through their application profile.
    clipboard_threshold: int = 0
    restore_clipboard: bool = True
    restore_delay_ms: int = 300
    key_delay_ms: int = 1
    replace_selection: bool = True
    focus_settle_ms: int = 25


@dataclass
class PrivacySettings:
    store_history: bool = True
    store_audio: bool = False             # OFF by default, see PRIVACY.md
    history_retention_days: int = 90
    audio_retention_days: int = 7
    telemetry: bool = False
    redact_history_in_ui: bool = False


@dataclass
class AppearanceSettings:
    theme: str = "system"                 # system | dark | light
    hud_position: str = "bottom_center"   # bottom_center | near_cursor | top_center
    hud_offset: int = 72
    reduced_motion: bool = False
    show_partials: bool = True
    show_waveform: bool = True
    high_contrast: bool = False


@dataclass
class AdvancedSettings:
    developer_mode: bool = False
    log_level: str = "INFO"
    start_with_windows: bool = False
    start_minimized: bool = True
    show_onboarding: bool = True
    backend_port: int = 0                 # 0 -> ephemeral


@dataclass
class Settings:
    audio: AudioSettings = field(default_factory=AudioSettings)
    vad: VadSettings = field(default_factory=VadSettings)
    asr: AsrSettings = field(default_factory=AsrSettings)
    llm: LlmSettings = field(default_factory=LlmSettings)
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    hotkeys: HotkeySettings = field(default_factory=HotkeySettings)
    injection: InjectionSettings = field(default_factory=InjectionSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    advanced: AdvancedSettings = field(default_factory=AdvancedSettings)


_SECTIONS = {f.name: f.type for f in dataclasses.fields(Settings)}


def settings_to_dict(s: Settings) -> Dict[str, Any]:
    return dataclasses.asdict(s)


def _apply(obj: Any, values: Dict[str, Any]) -> Any:
    """Assign known fields onto a dataclass instance, ignoring unknown keys."""
    valid = {f.name: f for f in dataclasses.fields(obj)}
    for key, value in (values or {}).items():
        spec = valid.get(key)
        if spec is None:
            continue
        setattr(obj, key, _coerce(spec.type, value, getattr(obj, key)))
    return obj


def _coerce(type_hint: Any, value: Any, current: Any) -> Any:
    """Best-effort coercion so values arriving as JSON land in the right type."""
    hint = type_hint if isinstance(type_hint, str) else getattr(type_hint, "__name__", "")
    try:
        if value is None:
            return None if "None" in hint or current is None else current
        if hint.startswith("int"):
            return int(value)
        if hint.startswith("float"):
            return float(value)
        if hint.startswith("bool"):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if hint.startswith("str"):
            return str(value)
    except (TypeError, ValueError):
        return current
    return value


def settings_from_dict(data: Dict[str, Any] | None) -> Settings:
    s = Settings()
    if not data:
        return s
    for section in _SECTIONS:
        if section in data and isinstance(data[section], dict):
            _apply(getattr(s, section), data[section])
    return s


def section_names() -> list[str]:
    return list(_SECTIONS)
