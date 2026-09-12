import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useState } from "react";

import { Shell, type Route } from "./components/layout/Shell";
import { Button, Spinner } from "./components/ui";
import { Icon } from "./components/ui/Icon";
import { AboutPage } from "./pages/AboutPage";
import { Dashboard } from "./pages/Dashboard";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { HistoryPage } from "./pages/HistoryPage";
import { ModelsPage } from "./pages/ModelsPage";
import { Onboarding } from "./pages/Onboarding";
import { PrivacyPage } from "./pages/PrivacyPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SnippetsPage } from "./pages/SnippetsPage";
import { VocabularyPage } from "./pages/VocabularyPage";
import { api, on, onBackend } from "./services/bridge";
import { applyTheme, useAppStore } from "./stores/appStore";
import type { AsrStatus, PullProgressEvent } from "./types";

export function App() {
  const [route, setRoute] = useState<Route>("dashboard");
  const store = useAppStore();
  const { settings, loading, fatalError, ready } = store;
  const [showOnboarding, setShowOnboarding] = useState(false);

  const init = useAppStore((s) => s.init);
  const refreshSettings = useAppStore((s) => s.refreshSettings);
  const refreshLlm = useAppStore((s) => s.refreshLlm);
  const setAsrStatus = useAppStore((s) => s.setAsrStatus);
  const setPull = useAppStore((s) => s.setPull);
  const toast = useAppStore((s) => s.toast);
  const setFatal = useAppStore((s) => s.setFatal);

  useEffect(() => {
    void init();
  }, [init]);

  useEffect(() => {
    if (settings?.advanced.show_onboarding) setShowOnboarding(true);
  }, [settings?.advanced.show_onboarding]);

  // The system theme can change while the window is open.
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const handler = () => applyTheme(useAppStore.getState().settings);
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, []);

  useEffect(() => {
    const subscriptions = [
      on<{ route: string }>("localflow:navigate", (payload) => {
        const target = payload.route as Route;
        if (target === "diagnostics" || target === "about") setRoute(target);
        else if (
          ["dashboard", "history", "vocabulary", "snippets", "settings", "models", "privacy"].includes(
            target,
          )
        ) {
          setRoute(target);
        }
      }),
      on<{ code: string; message: string; fatal?: boolean }>("localflow:error", (payload) => {
        // Only a backend that cannot be started at all is fatal. A microphone
        // or model problem is reported, but the window stays usable so the
        // user can go and fix it in Settings.
        if (payload.fatal && FATAL_CODES.has(payload.code)) setFatal(payload.message);
        else toast("error", errorTitle(payload.code), payload.message);
        // A microphone problem usually means the stream moved to a different
        // device; re-read which one so the interface stops naming the old one.
        if (payload.code.startsWith("mic")) {
          void useAppStore.getState().refreshDevices();
        }
      }),
      on<{ ready: boolean }>("localflow:backend-ready", () => {
        void init();
      }),
      on<{ paused: boolean }>("localflow:paused", () => {
        void api.isPaused().then((paused) => useAppStore.setState({ paused }));
      }),
      onBackend<AsrStatus>("asr.status", (status) => setAsrStatus(status)),
      onBackend<PullProgressEvent>("llm.pull", (progress) => {
        if (progress.error) {
          setPull(null);
          toast("error", "Download failed", progress.error);
          return;
        }
        if (progress.done) {
          setPull(null);
          void refreshLlm();
          toast("success", `${progress.model} is ready`);
          return;
        }
        setPull({
          model: progress.model,
          percent: progress.percent ?? 0,
          status: progress.status,
        });
      }),
      onBackend<{ sections: string[] }>("settings.changed", () => {
        void refreshSettings();
      }),
      onBackend<Record<string, unknown>>("llm.status", () => {
        void refreshLlm();
      }),
    ];
    return () => {
      subscriptions.forEach((p) => void p.then((off) => off()));
    };
  }, [init, refreshLlm, refreshSettings, setAsrStatus, setFatal, setPull, toast]);

  const navigate = useCallback((next: Route) => setRoute(next), []);

  if (loading && !settings) return <Booting />;
  if (fatalError && !ready) return <FatalError message={fatalError} onRetry={() => void init()} />;

  return (
    <>
      <Shell route={route} onNavigate={navigate}>
        {route === "dashboard" && <Dashboard onNavigate={navigate} />}
        {route === "history" && <HistoryPage />}
        {route === "vocabulary" && <VocabularyPage />}
        {route === "snippets" && <SnippetsPage />}
        {route === "models" && <ModelsPage />}
        {route === "settings" && <SettingsPage onNavigate={navigate} />}
        {route === "privacy" && <PrivacyPage />}
        {route === "diagnostics" && <DiagnosticsPage />}
        {route === "about" && <AboutPage />}
      </Shell>

      <Toasts />

      <AnimatePresence>
        {showOnboarding && (
          <Onboarding
            onDone={() => {
              setShowOnboarding(false);
              void useAppStore
                .getState()
                .patch({ advanced: { show_onboarding: false } })
                .catch(() => undefined);
            }}
          />
        )}
      </AnimatePresence>
    </>
  );
}

function Booting() {
  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-4 bg-surface">
      <div className="nm-raised flex h-14 w-14 items-center justify-center rounded-2xl text-accent">
        <Spinner size={22} />
      </div>
      <p className="text-[13px] text-muted">Starting LocalFlow…</p>
      <p className="text-2xs text-faint">Loading the speech model</p>
    </div>
  );
}

function FatalError({ message, onRetry }: { message: string; onRetry(): void }) {
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-surface p-10">
      <div className="nm-raised w-full max-w-lg rounded-2xl p-7 text-center">
        <div className="nm-inset mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl text-warning">
          <Icon name="warning" size={20} />
        </div>
        <h1 className="text-[16px] font-semibold text-ink">LocalFlow could not start</h1>
        <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-muted">{message}</p>
        <div className="mt-6 flex justify-center gap-2">
          <Button variant="primary" onClick={onRetry} icon={<Icon name="refresh" />}>
            Try again
          </Button>
          <Button
            onClick={() => void api.paths().then((p) => api.openPath(p.logs_dir))}
            icon={<Icon name="folder" />}
          >
            Open logs
          </Button>
        </div>
      </div>
    </div>
  );
}

function Toasts() {
  const toasts = useAppStore((s) => s.toasts);
  const dismiss = useAppStore((s) => s.dismissToast);

  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-80 flex-col gap-2">
      <AnimatePresence initial={false}>
        {toasts.map((item) => (
          <motion.div
            key={item.id}
            layout
            initial={{ opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, x: 16, scale: 0.97 }}
            transition={{ duration: 0.2, ease: [0.22, 0.61, 0.36, 1] }}
            className="nm-raised pointer-events-auto rounded-xl p-3.5"
            role="status"
          >
            <div className="flex items-start gap-2.5">
              <span className="mt-0.5">
                <Icon
                  name={item.kind === "error" ? "warning" : item.kind === "success" ? "check" : "info"}
                  className={
                    item.kind === "error"
                      ? "text-danger"
                      : item.kind === "success"
                        ? "text-positive"
                        : "text-accent"
                  }
                />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-ink">{item.title}</p>
                {item.detail && (
                  <p className="mt-1 break-words text-2xs leading-relaxed text-muted">
                    {item.detail}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                className="-mr-1 -mt-1 rounded-lg p-1 text-faint transition-colors hover:text-ink"
                aria-label="Dismiss"
              >
                <Icon name="close" size={13} />
              </button>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

/** Startup failures the interface cannot recover from on its own. */
const FATAL_CODES = new Set(["backend_missing", "backend_failed"]);

function errorTitle(code: string): string {
  if (code.startsWith("mic") || code.includes("microphone")) return "Microphone problem";
  if (code.startsWith("asr")) return "Speech engine problem";
  if (code.startsWith("llm")) return "Local model problem";
  if (code.startsWith("hotkey")) return "Shortcut problem";
  return "Something went wrong";
}
