import { useEffect, useState } from "react";

import { Button, Card, PageHeader, Section, StatusDot } from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api, BridgeError } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { Diagnostics, QualitySnapshot } from "../types";

/** Developer-mode panel: the numbers behind the last dictation. */
export function DiagnosticsPage() {
  const [info, setInfo] = useState<Diagnostics | null>(null);
  const [quality, setQuality] = useState<QualitySnapshot | null>(null);
  const [probe, setProbe] = useState<Record<string, unknown> | null>(null);
  const [sample, setSample] = useState(
    "uh can you send Rahul the quotation tomorrow, actually no, Friday",
  );
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useAppStore((s) => s.toast);

  const refresh = () => {
    void api.diagnostics().then(setInfo).catch(() => undefined);
    void api.quality(30).then(setQuality).catch(() => undefined);
  };

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, []);

  const timings = (info?.last.timings ?? {}) as Record<string, number>;
  const stages: Array<[string, number]> = [
    ["Recording", timings.record_ms ?? 0],
    ["Voice detection", timings.vad_ms ?? 0],
    ["Transcription", timings.asr_ms ?? 0],
    ["Processing", timings.process_ms ?? 0],
    ["Local model", timings.llm_ms ?? 0],
  ];
  const pipelineTotal = stages.slice(1).reduce((sum, [, value]) => sum + value, 0);

  return (
    <div>
      <PageHeader
        title="Diagnostics"
        description="Only shown in developer mode. Nothing here is transmitted anywhere."
        actions={
          <Button onClick={refresh} icon={<Icon name="refresh" />}>
            Refresh
          </Button>
        }
      />

      <div className="mb-6 grid grid-cols-3 gap-3">
        <Card className="p-4">
          <p className="section-title mb-2.5">Speech engine</p>
          <KeyValue label="Model" value={info?.asr.model ?? "-"} />
          <KeyValue label="Device" value={info?.asr.device?.toUpperCase() ?? "-"} />
          <KeyValue label="Precision" value={info?.asr.compute_type ?? "-"} />
          <KeyValue label="Load time" value={`${info?.asr.load_seconds ?? 0}s`} />
          <KeyValue label="Language" value={info?.asr.last_language || "auto"} />
        </Card>
        <Card className="p-4">
          <p className="section-title mb-2.5">Hardware</p>
          <KeyValue label="GPU" value={info?.hardware.gpu_name || "none"} />
          <KeyValue
            label="VRAM"
            value={
              info?.hardware.vram_total_mb
                ? `${info.hardware.vram_free_mb} / ${info.hardware.vram_total_mb} MB free`
                : "-"
            }
          />
          <KeyValue label="Driver" value={info?.hardware.driver || "-"} />
          <KeyValue label="RAM free" value={`${info?.hardware.ram.available ?? 0} MB`} />
          <KeyValue label="CPU threads" value={String(info?.hardware.cpu_count ?? "-")} />
        </Card>
        <Card className="p-4">
          <p className="section-title mb-2.5">Runtime</p>
          <KeyValue label="Version" value={info?.version ?? "-"} />
          <KeyValue label="VAD" value={info?.vad.backend ?? "-"} />
          <KeyValue
            label="Audio"
            value={
              info?.audio.streaming
                ? `${info.audio.stream_rate} Hz${info.audio.overflows ? ` · ${info.audio.overflows} overflows` : ""}`
                : "closed"
            }
          />
          <KeyValue label="Vocabulary" value={`${info?.vocabulary_terms ?? 0} terms`} />
          <KeyValue label="Local model" value={info?.llm.model || "none"} />
        </Card>
      </div>

      <Section
        title="Quality"
        description="Measured from your own dictations, not from a test suite. A dictation counts as edited if you changed it in History, undid it, or said “undo that”."
      >
        <Card className="p-5">
          {!quality || quality.inserted === 0 ? (
            <p className="text-2xs text-muted">
              No inserted dictations in the last 30 days yet. This fills in as you use LocalFlow.
            </p>
          ) : (
            <>
              <div className="mb-5 grid grid-cols-4 gap-5">
                <BigStat
                  label="Zero-edit rate"
                  value={`${(quality.zero_edit_rate * 100).toFixed(1)}%`}
                  detail={`${quality.inserted - quality.edited} of ${quality.inserted} accepted as-is`}
                  tone={quality.zero_edit_rate >= 0.9 ? "positive" : "warning"}
                />
                <BigStat
                  label="Edited"
                  value={String(quality.edited)}
                  detail={`${quality.undone} undone`}
                  tone="neutral"
                />
                <BigStat
                  label="AI cleanup used"
                  value={`${(quality.llm_rate * 100).toFixed(0)}%`}
                  detail="of dictations"
                  tone="neutral"
                />
                <BigStat
                  label="End-to-end"
                  value={
                    quality.latency.total_ms_median
                      ? `${quality.latency.total_ms_median.toFixed(0)} ms`
                      : "-"
                  }
                  detail={
                    quality.latency.total_ms_p95
                      ? `p95 ${quality.latency.total_ms_p95.toFixed(0)} ms`
                      : "median"
                  }
                  tone="neutral"
                />
              </div>
              {quality.sample_is_small && (
                <p className="hint mb-4">
                  Fewer than 20 dictations so far - treat these numbers as indicative.
                </p>
              )}
              {quality.by_app.length > 0 && (
                <div>
                  <p className="section-title mb-2">By application</p>
                  <ul>
                    {quality.by_app.map((row) => (
                      <li
                        key={row.app}
                        className="flex items-center gap-3 border-b border-line/40 py-1.5 last:border-0"
                      >
                        <span className="w-40 shrink-0 truncate text-2xs text-ink">{row.app}</span>
                        <div className="nm-inset h-1.5 flex-1 overflow-hidden rounded-full">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${row.zero_edit_rate * 100}%`,
                              background:
                                row.zero_edit_rate >= 0.9
                                  ? "rgb(var(--positive))"
                                  : "rgb(var(--warning))",
                            }}
                          />
                        </div>
                        <span className="w-28 shrink-0 text-right font-mono text-2xs tabular-nums text-muted">
                          {(row.zero_edit_rate * 100).toFixed(0)}% · {row.dictations}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </Card>
      </Section>

      <Section title="Last dictation" description="Every stage of the most recent utterance.">
        <Card className="p-5">
          <div className="mb-5">
            {stages.map(([label, value]) => (
              <div key={label} className="mb-2 flex items-center gap-3">
                <span className="w-32 shrink-0 text-2xs text-muted">{label}</span>
                <div className="nm-inset h-2.5 flex-1 overflow-hidden rounded-full">
                  <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{
                      width: `${pipelineTotal > 0 && label !== "Recording" ? Math.min(100, (value / pipelineTotal) * 100) : value > 0 ? 100 : 0}%`,
                      background:
                        label === "Recording"
                          ? "rgb(var(--faint))"
                          : label === "Local model"
                            ? "rgb(var(--warning))"
                            : "rgb(var(--accent))",
                    }}
                  />
                </div>
                <span className="w-20 shrink-0 text-right font-mono text-2xs tabular-nums text-ink">
                  {value.toFixed(0)} ms
                </span>
              </div>
            ))}
            <div className="mt-3 flex items-center justify-between border-t border-line/60 pt-3">
              <span className="text-2xs font-medium text-ink">
                Release to inserted text
              </span>
              <span className="font-mono text-[13px] font-semibold tabular-nums text-accent">
                {pipelineTotal.toFixed(0)} ms
              </span>
            </div>
          </div>

          <Transcript label="What was heard" text={info?.last.raw ?? ""} />
          <Transcript label="After deterministic processing" text={info?.last.deterministic ?? ""} />
          <Transcript label="Inserted" text={info?.last.final ?? ""} accent />
          {info?.last.diagnostics && Object.keys(info.last.diagnostics).length > 0 && (
            <details className="mt-3">
              <summary className="cursor-pointer text-2xs text-faint hover:text-muted">
                Full diagnostic payload
              </summary>
              <pre className="well mt-2 max-h-72 overflow-auto p-3 font-mono text-2xs leading-relaxed text-muted">
                {JSON.stringify(info.last.diagnostics, null, 2)}
              </pre>
            </details>
          )}
        </Card>
      </Section>

      <Section
        title="Pipeline sandbox"
        description="Run text straight through the processing pipeline without speaking."
      >
        <Card className="p-5">
          <textarea
            className="input min-h-[80px] resize-y font-sans"
            value={sample}
            onChange={(e) => setSample(e.target.value)}
            aria-label="Sample text"
          />
          <div className="mt-3 flex gap-2">
            <Button
              variant="primary"
              busy={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  setResult(await api.processText(sample, { exe: "notepad.exe" }));
                } catch (error) {
                  toast("error", "Failed", error instanceof BridgeError ? error.message : "");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Run pipeline
            </Button>
            <Button
              busy={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  setResult(await api.processText(sample, { exe: "notepad.exe" }, "", false));
                } catch (error) {
                  toast("error", "Failed", error instanceof BridgeError ? error.message : "");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Deterministic only
            </Button>
          </div>
          {result && (
            <pre className="well mt-3 max-h-80 overflow-auto p-3 font-mono text-2xs leading-relaxed text-muted">
              {JSON.stringify(result, null, 2)}
            </pre>
          )}
        </Card>
      </Section>

      <Section
        title="Focused window"
        description="What LocalFlow can see about the application you last had in front."
      >
        <Card className="p-5">
          <Button
            onClick={() =>
              void api
                .probeContext()
                .then((value) => setProbe(value as unknown as Record<string, unknown>))
                .catch((error) =>
                  toast("error", "Probe failed", error instanceof BridgeError ? error.message : ""),
                )
            }
            icon={<Icon name="search" />}
          >
            Inspect focused window
          </Button>
          <p className="hint mt-2">
            Click into another application first - probing while this window has focus just
            describes LocalFlow.
          </p>
          {probe && (
            <pre className="well mt-3 max-h-64 overflow-auto p-3 font-mono text-2xs leading-relaxed text-muted">
              {JSON.stringify(probe, null, 2)}
            </pre>
          )}
        </Card>
      </Section>

      {info?.ready_error && (
        <Card className="p-4">
          <div className="flex items-start gap-2.5">
            <StatusDot tone="danger" />
            <div>
              <p className="text-[13px] font-medium text-ink">Startup warning</p>
              <p className="hint mt-1">{info.ready_error}</p>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

function BigStat({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: "positive" | "warning" | "neutral";
}) {
  const colour =
    tone === "positive"
      ? "text-positive"
      : tone === "warning"
        ? "text-warning"
        : "text-ink";
  return (
    <div>
      <p className="mb-1 text-2xs text-faint">{label}</p>
      <p className={`text-[22px] font-semibold tabular-nums leading-none tracking-tight ${colour}`}>
        {value}
      </p>
      <p className="mt-1 text-2xs text-muted">{detail}</p>
    </div>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="shrink-0 text-2xs text-faint">{label}</span>
      <span className="truncate text-right text-2xs font-medium text-ink" title={value}>
        {value}
      </span>
    </div>
  );
}

function Transcript({
  label,
  text,
  accent,
}: {
  label: string;
  text: string;
  accent?: boolean;
}) {
  return (
    <div className="mb-3">
      <p className="section-title mb-1.5">{label}</p>
      <p
        className={`well whitespace-pre-wrap p-3 text-2xs leading-relaxed ${
          accent ? "text-ink" : "text-muted"
        }`}
      >
        {text || "-"}
      </p>
    </div>
  );
}
