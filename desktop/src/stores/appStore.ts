/** Application state for the settings window. */
import { create } from "zustand";

import { api, BridgeError } from "../services/bridge";
import type {
  AsrStatus,
  AudioDevice,
  CommandInfo,
  Hardware,
  LlmStatus,
  ModelSpec,
  Settings,
  SettingsPatch,
  StyleInfo,
} from "../types";

/** The microphone LocalFlow actually has open - which is not always the one
 *  that is configured, and knowing the difference is the whole point. */
export interface ActiveDevice {
  id: number | null;
  name: string;
  hostApi: string;
  isAlias: boolean;
  silentSeconds: number;
  streaming: boolean;
}

export interface Toast {
  id: number;
  kind: "info" | "success" | "error";
  title: string;
  detail?: string;
}

interface AppState {
  ready: boolean;
  loading: boolean;
  fatalError: string;
  settings: Settings | null;
  hardware: Hardware | null;
  devices: AudioDevice[];
  deviceError: string;
  deviceTotal: number;
  showAllDevices: boolean;
  activeDevice: ActiveDevice;
  asr: AsrStatus | null;
  asrCatalog: ModelSpec[];
  downloadedModels: string[];
  llm: LlmStatus | null;
  styles: StyleInfo[];
  commands: CommandInfo[];
  paused: boolean;
  autostart: boolean;
  toasts: Toast[];
  pull: { model: string; percent: number; status: string } | null;

  init(): Promise<void>;
  refreshSettings(): Promise<void>;
  patch(patch: SettingsPatch): Promise<void>;
  refreshHardware(): Promise<void>;
  refreshModels(): Promise<void>;
  refreshLlm(): Promise<void>;
  refreshDevices(showAll?: boolean): Promise<void>;
  rescanDevices(showAll?: boolean): Promise<void>;
  setPaused(paused: boolean): Promise<void>;
  setAutostart(enabled: boolean): Promise<void>;
  setAsrStatus(status: Partial<AsrStatus>): void;
  setPull(pull: AppState["pull"]): void;
  toast(kind: Toast["kind"], title: string, detail?: string): void;
  dismissToast(id: number): void;
  setFatal(message: string): void;
}

let toastId = 0;

/** How long to wait for the backend before calling it a failure. Model
 *  loading happens after the socket connects, so this only covers process
 *  spawn plus the handshake. */
const BACKEND_WAIT_MS = 60_000;

export const useAppStore = create<AppState>((set, get) => ({
  ready: false,
  loading: true,
  fatalError: "",
  settings: null,
  hardware: null,
  devices: [],
  deviceError: "",
  deviceTotal: 0,
  showAllDevices: false,
  activeDevice: { id: null, name: "", hostApi: "", isAlias: false, silentSeconds: 0, streaming: false },
  asr: null,
  asrCatalog: [],
  downloadedModels: [],
  llm: null,
  styles: [],
  commands: [],
  paused: false,
  autostart: false,
  toasts: [],
  pull: null,

  async init() {
    set({ loading: true, fatalError: "" });
    try {
      // Wait for Rust to finish connecting to the Python backend.
      //
      // The `localflow:backend-ready` event usually fires before this webview
      // has mounted its listener, and Tauri does not replay events - so relying
      // on it alone leaves the window stuck on "could not start" even though
      // the engine came up fine. Poll the live connection state instead; the
      // event stays as the fast path.
      const deadline = Date.now() + BACKEND_WAIT_MS;
      let connected = await api.ready().catch(() => false);
      while (!connected) {
        if (get().fatalError) return; // a fatal event arrived; stop waiting
        if (Date.now() > deadline) {
          throw new BridgeError({
            code: "backend_timeout",
            message:
              "LocalFlow's speech engine did not start. Check the logs, then try again.",
            detail: "",
          });
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
        connected = await api.ready().catch(() => false);
      }

      const settings = await api.getSettings();
      set({ settings, ready: true, fatalError: "" });
      applyTheme(settings);

      // Everything below is non-blocking detail; the window is usable without it.
      const [catalogues, paused, autostart] = await Promise.allSettled([
        api.catalogues(),
        api.isPaused(),
        api.getAutostart(),
      ]);
      if (catalogues.status === "fulfilled") {
        set({ styles: catalogues.value.styles, commands: catalogues.value.commands });
      }
      if (paused.status === "fulfilled") set({ paused: paused.value });
      if (autostart.status === "fulfilled") set({ autostart: autostart.value });

      void get().refreshDevices();
      void get().refreshHardware();
      void get().refreshModels();
      void get().refreshLlm();
    } catch (error) {
      const message =
        error instanceof BridgeError ? error.message : "LocalFlow's engine is not responding.";
      set({ fatalError: message, ready: false });
    } finally {
      set({ loading: false });
    }
  },

  async refreshSettings() {
    const settings = await api.getSettings();
    set({ settings });
    applyTheme(settings);
  },

  async patch(patch) {
    const previous = get().settings;
    // Optimistic: settings toggles should feel instant.
    if (previous) {
      const next = structuredClone(previous) as Settings;
      for (const [section, values] of Object.entries(patch)) {
        Object.assign((next as any)[section], values);
      }
      set({ settings: next });
      applyTheme(next);
    }
    try {
      const settings = await api.updateSettings(patch);
      set({ settings });
      applyTheme(settings);
    } catch (error) {
      if (previous) {
        set({ settings: previous });
        applyTheme(previous);
      }
      const message = error instanceof BridgeError ? error.message : String(error);
      get().toast("error", "Could not save that setting", message);
      throw error;
    }
  },

  async refreshHardware() {
    try {
      set({ hardware: await api.hardware(true) });
    } catch {
      /* diagnostics only */
    }
  },

  async refreshModels() {
    try {
      const models = await api.asrModels();
      set({
        asrCatalog: models.catalog,
        asr: models.current,
        downloadedModels: models.downloaded,
      });
    } catch {
      /* diagnostics only */
    }
  },

  async refreshLlm() {
    try {
      set({ llm: await api.llmStatus() });
    } catch {
      /* diagnostics only */
    }
  },

  async refreshDevices(showAll?: boolean) {
    await loadDevices(set, get, showAll, false);
  },

  async rescanDevices(showAll?: boolean) {
    await loadDevices(set, get, showAll, true);
  },

  async setPaused(paused) {
    await api.setPaused(paused);
    set({ paused });
  },

  async setAutostart(enabled) {
    try {
      await api.setAutostart(enabled);
      set({ autostart: enabled });
    } catch (error) {
      get().toast(
        "error",
        "Could not change the startup setting",
        error instanceof BridgeError ? error.message : String(error),
      );
    }
  },

  setAsrStatus(status) {
    const current = get().asr;
    set({ asr: { ...(current ?? ({} as AsrStatus)), ...status } as AsrStatus });
  },

  setPull(pull) {
    set({ pull });
  },

  toast(kind, title, detail) {
    const id = ++toastId;
    set({ toasts: [...get().toasts, { id, kind, title, detail }] });
    const lifetime = kind === "error" ? 9000 : 3800;
    window.setTimeout(() => get().dismissToast(id), lifetime);
  },

  dismissToast(id) {
    set({ toasts: get().toasts.filter((t) => t.id !== id) });
  },

  setFatal(message) {
    set({ fatalError: message, loading: false });
  },
}));

/** Shared by refreshDevices and rescanDevices: the only difference is whether
 *  PortAudio is re-enumerated first, which also reopens the stream. */
async function loadDevices(
  set: (partial: Partial<AppState>) => void,
  get: () => AppState,
  showAll: boolean | undefined,
  rescan: boolean,
): Promise<void> {
  const all = showAll ?? get().showAllDevices;
  try {
    const result = rescan ? await api.rescanDevices(all) : await api.audioDevices(all);
    set({
      devices: result.devices,
      deviceError: result.error,
      deviceTotal: result.total,
      showAllDevices: all,
      activeDevice: {
        id: result.active_id ?? null,
        name: result.active_name ?? "",
        hostApi: result.active_host_api ?? "",
        isAlias: Boolean(result.active_is_alias),
        silentSeconds: result.silent_seconds ?? 0,
        streaming: Boolean(result.streaming),
      },
    });
  } catch (error) {
    set({ deviceError: error instanceof BridgeError ? error.message : String(error) });
  }
}

/** Reflect theme, contrast and motion preferences onto the document. */
export function applyTheme(settings: Settings | null) {
  const root = document.documentElement;
  const theme = settings?.appearance.theme ?? "system";
  const resolved =
    theme === "system"
      ? window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark"
      : theme;
  root.dataset.theme = resolved;
  root.dataset.contrast = settings?.appearance.high_contrast ? "high" : "normal";
  root.dataset.motion = settings?.appearance.reduced_motion ? "reduced" : "full";
}
