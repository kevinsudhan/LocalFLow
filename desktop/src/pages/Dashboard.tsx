import { motion } from "framer-motion";
import { useEffect, useState } from "react";

import type { Route } from "../components/layout/Shell";
import { Button, Card, Meter, PageHeader, StatusDot } from "../components/ui";
import { Icon, type IconName } from "../components/ui/Icon";
import { LatencyGraph } from "../components/ui/LatencyGraph";
import { api, onBackend } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { HistoryItem, HistoryStats, QualitySnapshot } from "../types";

export function Dashboard({ onNavigate }: { onNavigate(route: Route): void }) {
  const settings = useAppStore((s) => s.settings);
  const asr = useAppStore((s) => s.asr);
  const llm = useAppStore((s) => s.llm);
  const hardware = useAppStore((s) => s.hardware);
  const paused = useAppStore((s) => s.paused);

  const [stats, setStats] = useState<HistoryStats | null>(null);
  const [recent, setRecent] = useState<HistoryItem[]>([]);
  const [quality, setQuality] = useState<QualitySnapshot | null>(null);
  const [level, setLevel] = useState(0);

  const load = () => {
    void api
      .historyList("", 5)
      .then((result) => {
        setStats(result.stats);
        setRecent(result.items);
      })
      .catch(() => undefined);
    // Separate call, and separately allowed to fail: the dashboard is still
    // useful without the graph, and it must not be blank because one query was
    // slow.
    void api
      .quality(7)
      .then(setQuality)
      .catch(() => undefined);
  };

  useEffect(() => {
    void load();
    const subscriptions = [
      onBackend<{ id: number }>("history.added", () => void load()),
      onBackend<{ level: number }>("audio.level", (payload) => setLevel(payload.level)),
    ];
    return () => subscriptions.forEach((p) => void p.then((off) => off()));
  }, []);

  const hotkey = settings?.hotkeys.primary ?? "Ctrl+Space";
  const minutes = stats ? Math.round(stats.speech_ms / 60000) : 0;
  const series = quality?.llm_series ?? [];
  const llmCalls = series.filter((point) => point.llm_ran);
  const llmMedian = median(llmCalls.map((point) => point.llm_ms));
  const responseMedian = median(series.map((point) => point.response_ms));

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Hold your shortcut anywhere in Windows, speak, and let go."
      />

      <Card className="mb-6 overflow-hidden">
        <div className="flex items-center gap-6 p-6">
          <div className="nm-inset flex h-[74px] w-[74px] shrink-0 items-center justify-center rounded-2xl">
            <motion.div
              animate={
                paused
                  ? { scale: 1, opacity: 0.4 }
                  : { scale: 1 + Math.min(level * 4, 0.28), opacity: 1 }
              }
              transition={{ duration: 0.1 }}
              className="text-accent"
            >
              <Icon name="mic" size={28} />
            </motion.div>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <StatusDot tone={paused ? "warning" : asr?.ready ? "positive" : "neutral"} />
              <p className="text-[15px] font-semibold tracking-tight text-ink">
                {paused
                  ? "Dictation is paused"
                  : asr?.ready
                    ? "Ready to dictate"
                    : asr?.loading
                      ? "Loading the speech model…"
                      : "Preparing…"}
              </p>
            </div>
            <p className="mt-1.5 text-[13px] text-muted">
              Hold <kbd className="kbd mx-0.5">{hotkey}</kbd> in any application, speak naturally,
              then release. The text appears where your cursor is.
            </p>
            <div className="mt-3 max-w-xs">
              <Meter value={paused ? 0 : Math.min(1, level * 7)} tone="positive" />
              <p className="hint mt-1.5">Microphone level</p>
            </div>
          </div>

          <div className="flex shrink-0 flex-col gap-2">
            <Button
              variant="primary"
              onClick={() => void api.startDictation()}
              disabled={paused}
              icon={<Icon name="mic" />}
            >
              Start dictation
            </Button>
            <Button onClick={() => onNavigate("settings")} icon={<Icon name="keyboard" />}>
              Change shortcut
            </Button>
          </div>
        </div>
      </Card>

      <div className="mb-6 grid grid-cols-4 gap-3">
        <Stat label="Dictations today" value={stats?.today ?? 0} icon="mic" />
        <Stat label="Words today" value={stats?.today_words ?? 0} icon="sparkle" />
        <Stat label="Total dictations" value={stats?.total ?? 0} icon="history" />
        <Stat label="Minutes spoken" value={minutes} icon="pulse" />
      </div>

      <Card className="mb-4">
        <div className="flex items-baseline justify-between px-5 pb-1 pt-4">
          <div>
            <h2 className="text-[14px] font-semibold tracking-tight text-ink">Response time</h2>
            <p className="mt-0.5 text-2xs text-muted">
              From the moment you stop speaking to text on screen, across your last
              {" "}
              {series.length || "few"} dictations.
            </p>
          </div>
          <div className="flex gap-5 text-right">
            <Figure label="Median" value={responseMedian ? `${Math.round(responseMedian)} ms` : "-"} />
            <Figure
              label="Used AI"
              value={series.length ? `${Math.round((llmCalls.length / series.length) * 100)}%` : "-"}
            />
            <Figure label="AI median" value={llmMedian ? `${Math.round(llmMedian)} ms` : "none"} />
          </div>
        </div>
        <LatencyGraph points={series} />
      </Card>

      <div className="grid grid-cols-[1.5fr_1fr] gap-4">
        <Card>
          <div className="flex items-center justify-between px-5 pb-2 pt-4">
            <h2 className="text-[14px] font-semibold tracking-tight text-ink">Recent dictations</h2>
            <Button variant="quiet" onClick={() => onNavigate("history")}>
              View all
            </Button>
          </div>
          {recent.length === 0 ? (
            <div className="px-5 pb-6 pt-3">
              <p className="text-2xs text-muted">
                Nothing yet. Your first dictation will appear here.
              </p>
            </div>
          ) : (
            <ul className="pb-2">
              {recent.map((item) => (
                <li key={item.id} className="px-5 py-2.5">
                  <p className="line-clamp-2 text-[13px] leading-snug text-ink">
                    {item.final_text}
                  </p>
                  <div className="mt-1.5 flex items-center gap-2 text-2xs text-faint">
                    <span>{item.app_name || "Unknown app"}</span>
                    <span aria-hidden="true">·</span>
                    <span>{relativeTime(item.created_at)}</span>
                    {item.used_llm && (
                      <>
                        <span aria-hidden="true">·</span>
                        <span className="text-accent">refined</span>
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <div className="px-5 pb-3 pt-4">
            <h2 className="text-[14px] font-semibold tracking-tight text-ink">Engine</h2>
          </div>
          <div className="space-y-0 pb-2">
            <EngineRow
              label="Speech"
              value={asr?.label ?? asr?.model ?? "-"}
              meta={asr?.device ? `${asr.device.toUpperCase()} · ${asr.compute_type}` : "loading"}
              tone={asr?.ready ? "positive" : asr?.loading ? "warning" : "neutral"}
            />
            <EngineRow
              label="AI cleanup"
              value={llm?.enabled ? llm.model || "not selected" : "off"}
              meta={
                !llm?.enabled
                  ? "deterministic only"
                  : llm.available
                    ? "Ollama connected"
                    : "Ollama unavailable"
              }
              tone={!llm?.enabled ? "neutral" : llm.available && llm.model ? "positive" : "warning"}
            />
            <EngineRow
              label="Accelerator"
              value={hardware?.gpu_name || (hardware?.cuda_available ? "GPU" : "CPU")}
              meta={
                hardware?.vram_total_mb
                  ? `${Math.round(hardware.vram_free_mb / 1024)} GB free of ${Math.round(
                      hardware.vram_total_mb / 1024,
                    )} GB`
                  : `${hardware?.cpu_count ?? "?"} threads`
              }
              tone={hardware?.cuda_available ? "positive" : "neutral"}
            />
          </div>
          <div className="px-5 pb-4 pt-1">
            <Button className="w-full" onClick={() => onNavigate("models")} icon={<Icon name="chip" />}>
              Manage models
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-2xs text-faint">{label}</p>
      <p className="font-mono text-[13px] tabular-nums text-ink">{value}</p>
    </div>
  );
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function Stat({ label, value, icon }: { label: string; value: number; icon: IconName }) {
  return (
    <div className="nm-raised rounded-2xl p-4">
      <div className="mb-2 flex items-center gap-2 text-faint">
        <Icon name={icon} size={14} />
        <span className="text-2xs">{label}</span>
      </div>
      <p className="text-[24px] font-semibold tabular-nums leading-none tracking-tight text-ink">
        {value.toLocaleString()}
      </p>
    </div>
  );
}

function EngineRow({
  label,
  value,
  meta,
  tone,
}: {
  label: string;
  value: string;
  meta: string;
  tone: "positive" | "warning" | "neutral";
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-5 py-2.5">
      <div className="min-w-0">
        <p className="text-2xs text-faint">{label}</p>
        <p className="truncate text-[13px] font-medium text-ink" title={value}>
          {value}
        </p>
        <p className="truncate text-2xs text-muted">{meta}</p>
      </div>
      <StatusDot tone={tone} />
    </div>
  );
}

export function relativeTime(iso: string): string {
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(then).toLocaleDateString();
}
