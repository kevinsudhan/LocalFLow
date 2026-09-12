/**
 * The only place the UI talks to Rust.
 *
 * Every backend call is forwarded through the `backend_call` Tauri command, so
 * the renderer never learns the backend's port or token, and Rust can enforce
 * its own method allowlist.
 */
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

import type {
  AppProfile,
  AsrStatus,
  AudioDevice,
  BackendError,
  CommandInfo,
  CorrectionItem,
  Diagnostics,
  Hardware,
  HistoryItem,
  HistoryStats,
  LlmStatus,
  ModelSpec,
  PrivacyReport,
  ProbeContext,
  QualitySnapshot,
  Settings,
  SettingsPatch,
  SnippetItem,
  StyleInfo,
  VocabularyItem,
} from "../types";

export class BridgeError extends Error {
  code: string;
  detail: string;

  constructor(error: BackendError | string) {
    const payload: BackendError =
      typeof error === "string"
        ? { code: "unknown", message: error, detail: "" }
        : error;
    super(payload.message);
    this.name = "BridgeError";
    this.code = payload.code;
    this.detail = payload.detail ?? "";
  }
}

function toBridgeError(error: unknown): BridgeError {
  if (error instanceof BridgeError) return error;
  if (error && typeof error === "object" && "message" in error) {
    return new BridgeError(error as BackendError);
  }
  return new BridgeError(String(error));
}

async function call<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  try {
    return (await invoke("backend_call", { method, params })) as T;
  } catch (error) {
    throw toBridgeError(error);
  }
}

async function command<T>(name: string, args: Record<string, unknown> = {}): Promise<T> {
  try {
    return (await invoke(name, args)) as T;
  } catch (error) {
    throw toBridgeError(error);
  }
}

export interface HotkeyProbe {
  installed: boolean;
  enabled: boolean;
  primary: string;
  matches: number;
  key_without_chord: number;
}

export interface DeviceReport {
  devices: AudioDevice[];
  total: number;
  error: string;
  active_id: number | null;
  active_name: string;
  active_host_api: string;
  active_is_alias: boolean;
  silent_seconds: number;
  streaming: boolean;
}

export const api = {
  // -- lifecycle ---------------------------------------------------------
  ready: () => command<boolean>("backend_ready"),
  ping: () => call<{ pong: boolean }>("system.ping"),

  // -- settings ----------------------------------------------------------
  getSettings: () => call<Settings>("settings.get"),
  updateSettings: (patch: SettingsPatch) => call<Settings>("settings.update", { patch }),
  resetSettings: () => call<Settings>("settings.reset"),

  /** What the keyboard hook currently sees. Distinguishes "not installed",
   *  "disabled", and "the key arrives without its modifiers". */
  hotkeyProbe: () => invoke<HotkeyProbe>("hotkey_probe"),

  // -- hardware and models ----------------------------------------------
  hardware: (refresh = true) => call<Hardware>("system.hardware", { refresh }),
  audioDevices: (allDevices = false) =>
    call<DeviceReport>("audio.devices", { all_devices: allDevices }),
  /** Re-enumerate the audio hardware and reopen the stream. PortAudio caches
   *  the device list at start-up, so this is the only way a headset plugged in
   *  afterwards becomes visible. */
  rescanDevices: (allDevices = false) =>
    call<DeviceReport>("audio.rescan", { all_devices: allDevices }),
  asrModels: () =>
    call<{ catalog: ModelSpec[]; current: AsrStatus; downloaded: string[] }>("asr.models"),
  asrLoad: () => call<AsrStatus>("asr.load"),
  asrUnload: () => call<AsrStatus>("asr.unload"),
  asrWarm: () => call<AsrStatus & { warm_seconds: number }>("asr.warm"),

  llmStatus: () => call<LlmStatus>("llm.status"),
  llmTest: (model = "") =>
    call<{ ok: boolean; message: string; output?: string; latency_ms?: number }>("llm.test", {
      model,
    }),
  llmWarm: (model = "") => call<{ ok: boolean; model: string }>("llm.warm", { model }),
  llmUnload: (model = "") => call<{ ok: boolean; model: string }>("llm.unload", { model }),
  /** Download any model the Ollama registry knows about. */
  llmPull: (model: string, select = true) =>
    call<{ ok: boolean; model: string; bytes: number; seconds: number }>("llm.pull", {
      model,
      select,
    }),
  llmPullCancel: () => call<{ cancelled: boolean; model: string }>("llm.pull_cancel"),
  llmDelete: (model: string) =>
    call<{ ok: boolean; model: string; selected: string }>("llm.delete", { model }),
  llmShow: (model: string) => call<Record<string, unknown>>("llm.show", { model }),

  // -- dictation ---------------------------------------------------------
  startDictation: () => command<void>("start_dictation"),
  cancelDictation: () => command<void>("cancel_dictation"),
  undoDictation: () => command<void>("undo_dictation"),
  setPaused: (paused: boolean) => command<boolean>("set_paused", { paused }),
  isPaused: () => command<boolean>("is_paused"),
  processText: (
    text: string,
    context: Record<string, unknown> = {},
    style = "",
    forceLlm: boolean | null = null,
  ) =>
    call<{
      text: string;
      deterministic_text: string;
      used_llm: boolean;
      llm_reason: string;
      timings: Record<string, number>;
      warnings: string[];
      corrections: unknown[];
      removed_fillers: string[];
      context: Record<string, unknown>;
    }>("process.text", { text, context, style, force_llm: forceLlm }),

  // -- vocabulary --------------------------------------------------------
  vocabularyList: (search = "") =>
    call<{ items: VocabularyItem[] }>("vocabulary.list", { search }),
  vocabularyAdd: (
    term: string,
    soundsLike: string[] = [],
    category = "general",
    caseSensitive = true,
  ) =>
    call<{ item: VocabularyItem }>("vocabulary.add", {
      term,
      sounds_like: soundsLike,
      category,
      case_sensitive: caseSensitive,
    }),
  vocabularyUpdate: (id: number, fields: Partial<VocabularyItem>) =>
    call<{ item: VocabularyItem }>("vocabulary.update", { id, ...fields }),
  vocabularyDelete: (id: number) => call<{ ok: boolean }>("vocabulary.delete", { id }),
  vocabularyExport: () => call<{ items: Partial<VocabularyItem>[] }>("vocabulary.export"),
  vocabularyImport: (items: unknown[], replace = false) =>
    call<{ added: number }>("vocabulary.import", { items, replace }),

  // -- learned corrections ----------------------------------------------
  correctionsList: () => call<{ items: CorrectionItem[] }>("corrections.list"),
  correctionsObserve: (original: string, edited: string) =>
    call<{ learned: CorrectionItem[]; enabled: boolean }>("corrections.observe", {
      original,
      edited,
    }),
  correctionsDelete: (id: number) => call<{ ok: boolean }>("corrections.delete", { id }),
  correctionsToggle: (id: number, enabled: boolean) =>
    call<{ ok: boolean }>("corrections.toggle", { id, enabled }),
  correctionsClear: () => call<{ ok: boolean }>("corrections.clear"),

  // -- snippets ----------------------------------------------------------
  snippetsList: () => call<{ items: SnippetItem[] }>("snippets.list"),
  snippetsAdd: (name: string, trigger: string, expansion: string, mode = "replace_all") =>
    call<{ item: SnippetItem }>("snippets.add", { name, trigger, expansion, mode }),
  snippetsUpdate: (id: number, fields: Partial<SnippetItem>) =>
    call<{ item: SnippetItem }>("snippets.update", { id, ...fields }),
  snippetsDelete: (id: number) => call<{ ok: boolean }>("snippets.delete", { id }),

  // -- history -----------------------------------------------------------
  historyList: (search = "", limit = 100, offset = 0) =>
    call<{ items: HistoryItem[]; stats: HistoryStats }>("history.list", {
      search,
      limit,
      offset,
    }),
  historyDelete: (id: number) => call<{ ok: boolean }>("history.delete", { id }),
  historyClear: () => call<{ ok: boolean }>("history.clear"),
  historyEdit: (id: number, text: string) => call<{ ok: boolean }>("history.edit", { id, text }),
  historyStats: () => call<HistoryStats>("history.stats"),
  /** Zero-edit rate and latency percentiles from real usage. */
  quality: (days = 30) => call<QualitySnapshot>("metrics.quality", { days }),

  // -- profiles ----------------------------------------------------------
  profilesList: () => call<{ items: AppProfile[] }>("profiles.list"),
  profilesUpsert: (profile: Partial<AppProfile>) =>
    call<{ item: AppProfile }>("profiles.upsert", profile as Record<string, unknown>),
  profilesDelete: (id: number) => call<{ ok: boolean }>("profiles.delete", { id }),

  // -- system ------------------------------------------------------------
  catalogues: () =>
    call<{ styles: StyleInfo[]; commands: CommandInfo[]; categories: string[] }>(
      "system.catalogues",
    ),
  privacy: () => call<PrivacyReport>("system.privacy"),
  diagnostics: () => call<Diagnostics>("system.info"),
  exportAll: () => call<Record<string, unknown>>("system.export"),
  importAll: (payload: Record<string, unknown>) =>
    call<Record<string, number>>("system.import", { payload }),
  wipeAll: () => call<{ ok: boolean }>("system.wipe"),

  // -- Windows integration ----------------------------------------------
  validateHotkey: (shortcut: string) => command<string>("validate_hotkey", { shortcut }),
  probeContext: () => command<ProbeContext>("probe_context"),
  insertText: (text: string) => command<{ ok: boolean; method: string }>("insert_text", { text }),
  setAutostart: (enabled: boolean) => command<boolean>("set_autostart", { enabled }),
  getAutostart: () => command<boolean>("get_autostart"),
  openMainWindow: (route?: string) => command<void>("open_main_window", { route }),
  hideMainWindow: () => command<void>("hide_main_window"),
  quit: () => command<void>("quit_app"),
  paths: () =>
    command<{ data_dir: string; logs_dir: string; models_dir: string; audio_dir: string }>(
      "app_paths",
    ),
  openPath: (path: string) => command<void>("open_path", { path }),
};

/** Subscribe to a Tauri event. Returns the unsubscribe function. */
export function on<T>(event: string, handler: (payload: T) => void): Promise<UnlistenFn> {
  return listen<T>(event, (e) => handler(e.payload));
}

/** Subscribe to a backend-originated event (prefixed by Rust). */
export function onBackend<T>(event: string, handler: (payload: T) => void): Promise<UnlistenFn> {
  // Tauri event names may not contain a dot, so the Rust side rewrites the
  // separator before forwarding. Call sites keep using the backend's own
  // method names ("audio.level") and the translation stays in one place.
  return on<T>(`backend:${event.replace(/\./g, "-")}`, handler);
}
