/** Shapes shared with the Python backend. Keep in sync with localflow/config. */

export type Theme = "system" | "dark" | "light";
export type HotkeyMode = "push_to_talk" | "toggle" | "hands_free";
export type InjectionMethod = "auto" | "unicode" | "clipboard";
export type Aggressiveness = "conservative" | "balanced" | "aggressive";
export type HudPosition = "bottom_center" | "near_cursor" | "top_center";

export interface AudioSettings {
  device_id: number | null;
  device_name: string;
  sample_rate: number;
  channels: number;
  blocksize: number;
  pre_buffer_ms: number;
  post_buffer_ms: number;
  max_duration_s: number;
  input_gain: number;
  dc_offset_removal: boolean;
}

export interface VadSettings {
  enabled: boolean;
  threshold: number;
  min_speech_ms: number;
  min_silence_ms: number;
  speech_pad_ms: number;
  max_trim_lead_ms: number;
}

export interface AsrSettings {
  model: string;
  device: "auto" | "cuda" | "cpu";
  compute_type: string;
  language: string;
  task: string;
  beam_size: number;
  partial_beam_size: number;
  vad_filter: boolean;
  streaming_partials: boolean;
  partial_interval_ms: number;
  partial_min_audio_ms: number;
  condition_on_previous_text: boolean;
  temperature: number;
  use_vocabulary_prompt: boolean;
  cpu_threads: number;
  keep_warm: boolean;
  unload_after_idle_s: number;
}

export interface LlmSettings {
  enabled: boolean;
  provider: string;
  base_url: string;
  model: string;
  timeout_s: number;
  keep_alive: string;
  num_ctx: number;
  temperature: number;
  top_p: number;
  always_use: boolean;
  max_input_chars: number;
  max_output_ratio: number;
}

export interface ProcessingSettings {
  remove_fillers: boolean;
  filler_aggressiveness: Aggressiveness;
  resolve_corrections: boolean;
  auto_punctuation: boolean;
  auto_paragraphs: boolean;
  auto_lists: boolean;
  smart_capitalization: boolean;
  default_style: string;
  use_context: boolean;
  commands_enabled: boolean;
  snippets_enabled: boolean;
  learning_enabled: boolean;
  learning_threshold: number;
  vocabulary_enabled: boolean;
  vocabulary_fuzzy_threshold: number;
}

export interface HotkeySettings {
  primary: string;
  mode: HotkeyMode;
  cancel_key: string;
  tap_toggle_ms: number;
  hands_free_silence_ms: number;
  undo_hotkey: string;
  enabled: boolean;
}

export interface InjectionSettings {
  method: InjectionMethod;
  clipboard_threshold: number;
  restore_clipboard: boolean;
  restore_delay_ms: number;
  key_delay_ms: number;
  replace_selection: boolean;
  focus_settle_ms: number;
}

export interface PrivacySettings {
  store_history: boolean;
  store_audio: boolean;
  history_retention_days: number;
  audio_retention_days: number;
  telemetry: boolean;
  redact_history_in_ui: boolean;
}

export interface AppearanceSettings {
  theme: Theme;
  hud_position: HudPosition;
  hud_offset: number;
  reduced_motion: boolean;
  show_partials: boolean;
  show_waveform: boolean;
  high_contrast: boolean;
}

export interface AdvancedSettings {
  developer_mode: boolean;
  log_level: string;
  start_with_windows: boolean;
  start_minimized: boolean;
  show_onboarding: boolean;
  backend_port: number;
}

export interface Settings {
  audio: AudioSettings;
  vad: VadSettings;
  asr: AsrSettings;
  llm: LlmSettings;
  processing: ProcessingSettings;
  hotkeys: HotkeySettings;
  injection: InjectionSettings;
  privacy: PrivacySettings;
  appearance: AppearanceSettings;
  advanced: AdvancedSettings;
}

export type SettingsPatch = {
  [K in keyof Settings]?: Partial<Settings[K]>;
};

export interface AudioDevice {
  id: number;
  name: string;
  host_api: string;
  channels: number;
  default_samplerate: number;
  is_default: boolean;
  /** Lower is a better host API. Present on collapsed listings. */
  rank?: number;
  /** True for exclusive-mode (WDM-KS) endpoints, which often fail to open. */
  exclusive?: boolean;
  /** Device ids for the same microphone on other host APIs. */
  aliases?: number[];
}

export interface GpuInfo {
  name: string;
  vram_total_mb: number;
  vram_used_mb: number;
  vram_free_mb: number;
  driver: string;
}

export interface Hardware {
  cuda_available: boolean;
  cuda_device_count: number;
  gpu: GpuInfo | null;
  gpu_name: string;
  vram_total_mb: number;
  vram_free_mb: number;
  driver: string;
  compute_types_cuda: string[];
  compute_types_cpu: string[];
  ram: { total: number; available: number };
  cpu_count: number;
  recommendation?: { model: string; device: string; compute_type: string; reason: string };
}

export interface ModelSpec {
  key: string;
  label: string;
  repo: string;
  params: string;
  download_mb: number;
  vram_fp16_mb: number;
  ram_int8_mb: number;
  multilingual: boolean;
  quality: number;
  speed: number;
  note: string;
  fits_gpu: boolean;
  fits_cpu: boolean;
}

export interface AsrStatus {
  ready: boolean;
  loading: boolean;
  model: string;
  device: string;
  compute_type: string;
  error: string;
  load_seconds: number;
  last_language: string;
  estimated_mb: number;
  label: string;
  state?: string;
  message?: string;
}

export interface LlmModelInfo {
  name: string;
  size_bytes: number;
  size_gb: number;
  parameter_size: string;
  quantization: string;
  family: string;
}

export interface SuggestedModel {
  name: string;
  size_gb: number;
  note: string;
  recommended: boolean;
}

export interface LlmStatus {
  available: boolean;
  message: string;
  provider: string;
  base_url: string;
  model: string;
  enabled: boolean;
  models: LlmModelInfo[];
  running: Array<{ name: string; size_vram?: number; expires_at?: string }>;
  suggested: SuggestedModel[];
  downloading: string;
}

export interface PullProgressEvent {
  model: string;
  status: string;
  completed?: number;
  total?: number;
  percent?: number;
  done?: boolean;
  error?: string;
  code?: string;
}

export interface VocabularyItem {
  id: number;
  term: string;
  sounds_like: string[];
  category: string;
  case_sensitive: boolean;
  enabled: boolean;
  hits: number;
  created_at: string;
  updated_at: string;
}

export interface CorrectionItem {
  id: number;
  wrong: string;
  correct: string;
  occurrences: number;
  promoted: boolean;
  enabled: boolean;
  source: string;
  created_at: string;
  last_seen: string;
}

export interface SnippetItem {
  id: number;
  name: string;
  trigger: string;
  expansion: string;
  mode: string;
  enabled: boolean;
  uses: number;
}

export interface HistoryItem {
  id: number;
  created_at: string;
  app_exe: string;
  app_name: string;
  app_category: string;
  window_title: string;
  raw_transcript: string;
  final_text: string;
  language: string;
  style: string;
  duration_ms: number;
  used_llm: boolean;
  latency: Record<string, number>;
  audio_path: string | null;
  inserted: boolean;
  word_count: number;
}

export interface HistoryStats {
  total: number;
  words: number;
  speech_ms: number;
  today: number;
  today_words: number;
}

export interface AppProfile {
  id: number;
  pattern: string;
  match_on: string;
  app_name: string;
  category: string;
  style: string;
  llm_enabled: boolean;
  injection_method: string;
  builtin: boolean;
  enabled: boolean;
  priority: number;
}

export interface StyleInfo {
  key: string;
  label: string;
  description: string;
}

export interface CommandInfo {
  phrase: string;
  action: string;
  description: string;
}

export interface PrivacyReport {
  audio_processing: string;
  speech_recognition: string;
  ai_processing: string;
  history: string;
  audio_history: string;
  telemetry: string;
  network: Array<{ name: string; endpoint: string; scope: string; required: boolean }>;
  data_dir: string;
}

export interface QualitySnapshot {
  inserted: number;
  edited: number;
  undone: number;
  zero_edit_rate: number;
  llm_rate: number;
  latency: Record<string, number>;
  by_app: Array<{
    app: string;
    dictations: number;
    edited: number;
    zero_edit_rate: number;
    llm_rate: number;
  }>;
  window_days: number;
  sample_is_small: boolean;
}

export interface Diagnostics {
  version: string;
  hardware: Hardware;
  asr: AsrStatus;
  vad: { backend: string; ready: boolean; error: string; threshold: number };
  audio: {
    streaming: boolean;
    capturing: boolean;
    device_id: number | null;
    stream_rate: number;
    overflows: number;
    level: number;
  };
  llm: { model: string; enabled: boolean; available: boolean; running: unknown[] };
  vocabulary_terms: number;
  snippets: number;
  paused: boolean;
  data_dir: string;
  last: {
    raw: string;
    deterministic: string;
    final: string;
    timings: Record<string, number>;
    diagnostics: Record<string, unknown>;
  };
  history: HistoryStats;
  ready_error: string;
}

export type HudState =
  | "idle"
  | "listening"
  | "processing"
  | "inserting"
  | "success"
  | "error";

export interface HudPayload {
  state: HudState;
  text?: string;
  message?: string;
  app_name?: string;
  elapsed_ms?: number;
}

export interface SessionResult {
  session_id: string;
  text: string;
  insert_text: string;
  raw_transcript: string;
  deterministic_text: string;
  language: string;
  used_llm: boolean;
  llm_model: string;
  command: { action: string; argument: string } | null;
  snippet: { name: string; trigger: string } | null;
  replace_selection: boolean;
  timings: Record<string, number>;
  diagnostics: Record<string, any>;
  warnings: string[];
  had_speech: boolean;
  cancelled: boolean;
}

export interface BackendError {
  code: string;
  message: string;
  detail: string;
}

export interface ProbeContext {
  exe: string;
  window_title: string;
  selected_text: string;
  text_before: string;
  text_after: string;
  control_type: string;
  is_password: boolean;
  has_uia_text: boolean;
}
