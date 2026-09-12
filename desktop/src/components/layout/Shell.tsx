import { motion } from "framer-motion";
import type { ReactNode } from "react";

import { api } from "../../services/bridge";
import { useAppStore } from "../../stores/appStore";
import { Button, StatusDot, Toggle } from "../ui";
import { Icon, type IconName } from "../ui/Icon";

export type Route =
  | "dashboard"
  | "history"
  | "vocabulary"
  | "snippets"
  | "settings"
  | "models"
  | "diagnostics"
  | "privacy"
  | "about";

const NAV: Array<{ route: Route; label: string; icon: IconName }> = [
  { route: "dashboard", label: "Dashboard", icon: "home" },
  { route: "history", label: "History", icon: "history" },
  { route: "vocabulary", label: "Vocabulary", icon: "book" },
  { route: "snippets", label: "Snippets", icon: "snippet" },
  { route: "models", label: "Models", icon: "chip" },
  { route: "settings", label: "Settings", icon: "sliders" },
  { route: "privacy", label: "Privacy", icon: "shield" },
];

export function Shell({
  route,
  onNavigate,
  children,
}: {
  route: Route;
  onNavigate(next: Route): void;
  children: ReactNode;
}) {
  const paused = useAppStore((s) => s.paused);
  const setPaused = useAppStore((s) => s.setPaused);
  const settings = useAppStore((s) => s.settings);
  const asr = useAppStore((s) => s.asr);
  const developer = settings?.advanced.developer_mode ?? false;

  const items = developer
    ? [...NAV, { route: "diagnostics" as Route, label: "Diagnostics", icon: "pulse" as IconName }]
    : NAV;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-surface">
      <nav className="flex w-[228px] shrink-0 flex-col px-3 pb-3 pt-3" aria-label="Main">
        <div className="drag-region mb-5 flex items-center gap-2.5 px-2 pt-1.5">
          <Mark />
          <div className="min-w-0">
            <p className="text-[13.5px] font-semibold leading-tight tracking-tight text-ink">
              LocalFlow
            </p>
            <p className="text-2xs leading-tight text-faint">Everything stays on this PC</p>
          </div>
        </div>

        <ul className="flex-1 space-y-1">
          {items.map((item) => {
            const active = item.route === route;
            return (
              <li key={item.route}>
                <button
                  type="button"
                  onClick={() => onNavigate(item.route)}
                  aria-current={active ? "page" : undefined}
                  className={`relative flex w-full items-center gap-2.5 rounded-xl px-3 py-2
                              text-[13px] transition-colors duration-150
                              ${active ? "text-ink" : "text-muted hover:text-ink"}`}
                >
                  {active && (
                    <motion.span
                      layoutId="nav-active"
                      className="nm-inset absolute inset-0 rounded-xl"
                      transition={{ type: "spring", stiffness: 520, damping: 42 }}
                    />
                  )}
                  <span className="relative z-10 flex items-center gap-2.5">
                    <Icon
                      name={item.icon}
                      className={active ? "text-accent" : "text-faint"}
                    />
                    <span className={active ? "font-medium" : ""}>{item.label}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        <div className="nm-raised mt-3 rounded-xl p-3">
          <div className="mb-2.5 flex items-center gap-2">
            <StatusDot
              tone={paused ? "warning" : asr?.ready ? "positive" : asr?.loading ? "warning" : "neutral"}
            />
            <span className="text-2xs font-medium text-ink">
              {paused ? "Paused" : asr?.ready ? "Ready" : asr?.loading ? "Loading model" : "Starting"}
            </span>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-2xs text-muted">Dictation</span>
            <Toggle
              checked={!paused}
              onChange={(next) => void setPaused(!next)}
              label="Enable dictation"
            />
          </div>
          <div className="mt-2.5 flex items-center justify-between">
            <span className="text-2xs text-muted">Hold to talk</span>
            <kbd className="kbd">{settings?.hotkeys.primary ?? "Ctrl+Space"}</kbd>
          </div>
        </div>

        <Button
          variant="quiet"
          className="mt-2 w-full justify-start"
          onClick={() => onNavigate("about")}
          icon={<Icon name="info" />}
        >
          About
        </Button>
      </nav>

      <main className="relative min-w-0 flex-1 overflow-hidden">
        <div className="drag-region absolute inset-x-0 top-0 h-9" />
        <div className="h-full overflow-y-auto px-8 pb-10 pt-9">
          {/* Keyed, but deliberately not wrapped in AnimatePresence.

              `mode="wait"` holds the incoming page back until the outgoing
              one has finished animating out - and the outgoing page's
              content is already gone by then, because the router renders it
              conditionally on `route`. An exit that never settles therefore
              left the whole content area empty with no way back: every
              subsequent navigation rendered nothing at all, while the
              sidebar carried on working and made it look like the pages had
              disappeared. Fading the new page in needs no exit animation, so
              there is nothing left to wait on. */}
          <motion.div
            key={route}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.16, ease: [0.22, 0.61, 0.36, 1] }}
          >
            {children}
          </motion.div>
        </div>
      </main>
    </div>
  );
}

function Mark() {
  return (
    <div
      className="nm-raised-sm flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
      aria-hidden="true"
    >
      <svg width="19" height="19" viewBox="0 0 24 24" fill="none">
        {[
          { x: 3.2, h: 6 },
          { x: 7.6, h: 11 },
          { x: 12, h: 17 },
          { x: 16.4, h: 12 },
          { x: 20.8, h: 7 },
        ].map((bar, index) => (
          <rect
            key={index}
            x={bar.x - 1.1}
            y={12 - bar.h / 2}
            width="2.2"
            height={bar.h}
            rx="1.1"
            fill="rgb(var(--accent))"
            opacity={0.55 + index * 0.11}
          />
        ))}
      </svg>
    </div>
  );
}

export function openLogs() {
  void api.paths().then((paths) => api.openPath(paths.logs_dir));
}
