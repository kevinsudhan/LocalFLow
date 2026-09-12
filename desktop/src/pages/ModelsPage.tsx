import { motion } from "framer-motion";
import { useEffect, useState } from "react";

import {
  Badge,
  Button,
  Card,
  ConfirmButton,
  EmptyState,
  Meter,
  PageHeader,
  Section,
  Select,
  StatusDot,
  TextInput,
  Toggle,
} from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api, BridgeError } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { ModelSpec } from "../types";

export function ModelsPage() {
  const {
    settings,
    hardware,
    asr,
    asrCatalog,
    downloadedModels,
    llm,
    pull,
    patch,
    refreshModels,
    refreshLlm,
    refreshHardware,
    toast,
  } = useAppStore();

  const [busy, setBusy] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    void refreshModels();
    void refreshLlm();
    const timer = window.setInterval(() => void refreshHardware(), 8000);
    return () => window.clearInterval(timer);
  }, [refreshHardware, refreshLlm, refreshModels]);

  if (!settings) return null;

  const run = async (key: string, work: () => Promise<unknown>, success?: string) => {
    setBusy(key);
    try {
      await work();
      if (success) toast("success", success);
    } catch (error) {
      toast(
        "error",
        "That did not work",
        error instanceof BridgeError ? error.message : String(error),
      );
    } finally {
      setBusy("");
    }
  };

  const installed = new Set(
    downloadedModels.map((name) => name.split("/").pop()?.replace("faster-whisper-", "") ?? name),
  );

  return (
    <div>
      <PageHeader
        title="Models"
        description="Speech recognition and AI cleanup both run on this machine. Pick what fits your hardware."
        actions={
          <Button
            onClick={() => void Promise.all([refreshModels(), refreshLlm(), refreshHardware()])}
            icon={<Icon name="refresh" />}
          >
            Refresh
          </Button>
        }
      />

      {/* ---------------------------------------------------------- hardware */}
      <Card className="mb-7 p-5">
        <div className="grid grid-cols-4 gap-5">
          <HardwareStat
            label="Graphics"
            value={hardware?.gpu_name || "No CUDA GPU"}
            detail={hardware?.driver ? `Driver ${hardware.driver}` : "CPU inference"}
            tone={hardware?.cuda_available ? "positive" : "warning"}
          />
          <HardwareStat
            label="VRAM"
            value={
              hardware?.vram_total_mb
                ? `${(hardware.vram_free_mb / 1024).toFixed(1)} / ${(
                    hardware.vram_total_mb / 1024
                  ).toFixed(1)} GB free`
                : "-"
            }
            detail="Shared by Whisper and the language model"
            tone={hardware && hardware.vram_free_mb > 1500 ? "positive" : "warning"}
            meter={
              hardware?.vram_total_mb
                ? 1 - hardware.vram_free_mb / hardware.vram_total_mb
                : undefined
            }
          />
          <HardwareStat
            label="System memory"
            value={hardware ? `${(hardware.ram.available / 1024).toFixed(1)} GB free` : "-"}
            detail={hardware ? `${(hardware.ram.total / 1024).toFixed(0)} GB total` : ""}
            tone="neutral"
          />
          <HardwareStat
            label="CPU threads"
            value={String(hardware?.cpu_count ?? "-")}
            detail={hardware?.cuda_available ? "Used as fallback" : "Primary compute"}
            tone="neutral"
          />
        </div>
        {hardware?.recommendation && (
          <p className="mt-4 flex items-start gap-2 text-2xs text-muted">
            <Icon name="sparkle" className="mt-0.5 shrink-0 text-accent" size={13} />
            <span>{hardware.recommendation.reason}</span>
          </p>
        )}
      </Card>

      {/* ------------------------------------------------------------- speech */}
      <Section
        title="Speech recognition"
        description="Larger models transcribe names, numbers and other languages more accurately, but take longer and use more memory."
        actions={
          <>
            <Button
              busy={busy === "warm"}
              onClick={() => run("warm", () => api.asrWarm(), "Model warmed up")}
            >
              Warm up
            </Button>
            <Button
              busy={busy === "unload"}
              onClick={() =>
                run(
                  "unload",
                  async () => {
                    await api.asrUnload();
                    await refreshModels();
                  },
                  "Model unloaded",
                )
              }
            >
              Free memory
            </Button>
          </>
        }
      >
        <Card className="mb-3 p-4">
          <div className="flex items-center gap-3">
            <StatusDot tone={asr?.ready ? "positive" : asr?.loading ? "warning" : "neutral"} />
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-medium text-ink">
                {asr?.ready
                  ? `${asr.label} running on ${asr.device.toUpperCase()}`
                  : asr?.loading
                    ? "Loading…"
                    : "Not loaded"}
              </p>
              <p className="hint mt-0.5">
                {asr?.ready
                  ? `${asr.compute_type} · about ${asr.estimated_mb} MB · loaded in ${asr.load_seconds}s`
                  : asr?.error || "The model loads on first use."}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Select
                aria-label="Device"
                value={settings.asr.device}
                onChange={(e) =>
                  void patch({ asr: { device: e.target.value as "auto" | "cuda" | "cpu" } })
                }
              >
                <option value="auto">Automatic</option>
                <option value="cuda" disabled={!hardware?.cuda_available}>
                  GPU (CUDA)
                </option>
                <option value="cpu">CPU</option>
              </Select>
              <Select
                aria-label="Precision"
                value={settings.asr.compute_type}
                onChange={(e) => void patch({ asr: { compute_type: e.target.value } })}
              >
                <option value="auto">Auto precision</option>
                {(settings.asr.device === "cpu"
                  ? hardware?.compute_types_cpu
                  : hardware?.compute_types_cuda
                )?.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </Card>

        <div className="grid grid-cols-2 gap-3">
          {asrCatalog.map((model) => (
            <WhisperCard
              key={model.key}
              model={model}
              selected={settings.asr.model === model.key}
              downloaded={installed.has(model.key)}
              busy={busy === `asr:${model.key}`}
              onSelect={() =>
                run(
                  `asr:${model.key}`,
                  async () => {
                    await patch({ asr: { model: model.key } });
                    await refreshModels();
                  },
                  `Switched to ${model.label}`,
                )
              }
            />
          ))}
        </div>
      </Section>

      {/* ---------------------------------------------------------------- LLM */}
      <Section
        title="AI cleanup"
        description="An optional local language model fixes grammar and resolves spoken corrections. LocalFlow only calls it when the deterministic pipeline is not enough."
        actions={
          <Toggle
            checked={settings.llm.enabled}
            onChange={(next) => void patch({ llm: { enabled: next } })}
            label="Enable AI cleanup"
          />
        }
      >
        {!llm?.available && (
          <Card className="mb-3 p-4">
            <div className="flex items-start gap-3">
              <Icon name="warning" className="mt-0.5 text-warning" />
              <div className="flex-1">
                <p className="text-[13px] font-medium text-ink">
                  {llm?.message || "Ollama is not reachable."}
                </p>
                <p className="hint mt-1">
                  LocalFlow still works without it - the deterministic pipeline handles most
                  dictation on its own. Install Ollama from ollama.com to enable AI cleanup.
                </p>
              </div>
              <Button onClick={() => void refreshLlm()} icon={<Icon name="refresh" />}>
                Retry
              </Button>
            </div>
          </Card>
        )}

        {/* Download any model, not just what is already installed. */}
        <Card className="mb-3 p-4">
          <p className="label mb-1">Use any Ollama model</p>
          <p className="hint mb-3">
            Type any name from the Ollama library, or a Hugging Face GGUF repo such as{" "}
            <code className="font-mono">hf.co/user/repo:Q4_K_M</code>. LocalFlow downloads it for
            you. This is the only step that needs the internet.
          </p>
          <div className="flex gap-2">
            <TextInput
              value={customModel}
              placeholder="qwen2.5:7b-instruct"
              spellCheck={false}
              onChange={(e) => setCustomModel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && customModel.trim() && !pull) {
                  void run("pull", () => api.llmPull(customModel.trim()));
                  setCustomModel("");
                }
              }}
              disabled={Boolean(pull)}
            />
            <Button
              variant="primary"
              icon={<Icon name="download" />}
              disabled={!customModel.trim() || Boolean(pull)}
              onClick={() => {
                void run("pull", () => api.llmPull(customModel.trim()));
                setCustomModel("");
              }}
            >
              Download
            </Button>
          </div>

          {pull && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              className="mt-4"
            >
              <div className="mb-1.5 flex items-center justify-between text-2xs">
                <span className="font-medium text-ink">{pull.model}</span>
                <span className="text-muted">
                  {pull.status} · {pull.percent.toFixed(0)}%
                </span>
              </div>
              <Meter value={pull.percent / 100} />
              <div className="mt-2 flex justify-end">
                <Button variant="quiet" onClick={() => void api.llmPullCancel()}>
                  Cancel
                </Button>
              </div>
            </motion.div>
          )}

          {!pull && llm?.suggested?.length ? (
            <div className="mt-4">
              <p className="section-title mb-2">Suggested for your hardware</p>
              <div className="flex flex-wrap gap-2">
                {llm.suggested
                  .filter((s) => !llm.models.some((m) => m.name === s.name))
                  .map((suggestion) => (
                    <button
                      key={suggestion.name}
                      type="button"
                      onClick={() => void run("pull", () => api.llmPull(suggestion.name))}
                      className="nm-raised-sm group rounded-xl px-3 py-2 text-left transition-colors"
                      title={suggestion.note}
                    >
                      <span className="flex items-center gap-2 text-2xs font-medium text-ink">
                        <Icon name="download" size={12} className="text-accent" />
                        {suggestion.name}
                        {suggestion.recommended && (
                          <span className="text-accent">· recommended</span>
                        )}
                      </span>
                      <span className="mt-0.5 block text-2xs text-muted">
                        {suggestion.size_gb} GB · {suggestion.note}
                      </span>
                    </button>
                  ))}
              </div>
            </div>
          ) : null}
        </Card>

        <Card>
          <div className="flex items-center justify-between px-5 pb-2 pt-4">
            <h3 className="text-[13px] font-semibold text-ink">Installed models</h3>
            <div className="flex gap-2">
              <Button
                busy={busy === "test"}
                disabled={!settings.llm.model}
                onClick={() =>
                  run("test", async () => {
                    const result = await api.llmTest();
                    setTestResult({
                      ok: result.ok,
                      text: result.ok
                        ? `${result.output ?? ""} (${Math.round(result.latency_ms ?? 0)} ms)`
                        : result.message,
                    });
                  })
                }
              >
                Test
              </Button>
              <Button
                busy={busy === "llmwarm"}
                disabled={!settings.llm.model}
                onClick={() => run("llmwarm", () => api.llmWarm(), "Model loaded into memory")}
              >
                Warm up
              </Button>
              <Button
                busy={busy === "llmunload"}
                disabled={!settings.llm.model}
                onClick={() => run("llmunload", () => api.llmUnload(), "Released from memory")}
              >
                Free memory
              </Button>
            </div>
          </div>

          {testResult && (
            <div className="mx-5 mb-2 rounded-xl px-3 py-2 text-2xs nm-inset">
              <span className={testResult.ok ? "text-positive" : "text-danger"}>
                {testResult.ok ? "Responded: " : "Failed: "}
              </span>
              <span className="text-muted">{testResult.text}</span>
            </div>
          )}

          {!llm || llm.models.length === 0 ? (
            <EmptyState
              icon={<Icon name="chip" size={22} />}
              title="No models installed"
              description="Download one above to enable AI cleanup."
            />
          ) : (
            <ul className="pb-3">
              {llm.models.map((model) => {
                const selected = settings.llm.model === model.name;
                const resident = llm.running.some((r) => r.name === model.name);
                return (
                  <li key={model.name} className="flex items-center gap-3 px-5 py-2.5">
                    <StatusDot tone={selected ? "positive" : resident ? "warning" : "neutral"} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-medium text-ink">{model.name}</p>
                      <p className="hint">
                        {model.parameter_size || "?"} · {model.quantization || "?"} ·{" "}
                        {model.size_gb} GB
                        {resident && " · in memory"}
                      </p>
                    </div>
                    {selected ? (
                      <Badge tone="accent">In use</Badge>
                    ) : (
                      <Button
                        onClick={() =>
                          run(
                            `use:${model.name}`,
                            () => patch({ llm: { model: model.name } }),
                            `Using ${model.name}`,
                          )
                        }
                        busy={busy === `use:${model.name}`}
                      >
                        Use
                      </Button>
                    )}
                    <ConfirmButton
                      variant="quiet"
                      confirmLabel="Delete?"
                      onConfirm={() =>
                        void run(
                          `del:${model.name}`,
                          async () => {
                            await api.llmDelete(model.name);
                            await refreshLlm();
                          },
                          `Deleted ${model.name}`,
                        )
                      }
                    >
                      <Icon name="trash" />
                    </ConfirmButton>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </Section>
    </div>
  );
}

function HardwareStat({
  label,
  value,
  detail,
  tone,
  meter,
}: {
  label: string;
  value: string;
  detail: string;
  tone: "positive" | "warning" | "neutral";
  meter?: number;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5">
        <StatusDot tone={tone} />
        <span className="text-2xs text-faint">{label}</span>
      </div>
      <p className="truncate text-[13px] font-medium text-ink" title={value}>
        {value}
      </p>
      <p className="mt-0.5 truncate text-2xs text-muted">{detail}</p>
      {meter !== undefined && (
        <div className="mt-2">
          <Meter value={meter} />
        </div>
      )}
    </div>
  );
}

function WhisperCard({
  model,
  selected,
  downloaded,
  busy,
  onSelect,
}: {
  model: ModelSpec;
  selected: boolean;
  downloaded: boolean;
  busy: boolean;
  onSelect(): void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={busy}
      aria-pressed={selected}
      className={`rounded-2xl p-4 text-left transition-all duration-200
                  ${selected ? "nm-inset" : "nm-raised hover:-translate-y-px"}`}
    >
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-[13px] font-semibold text-ink">{model.label}</span>
        {selected && <Badge tone="accent">Selected</Badge>}
        {!downloaded && <Badge>Downloads on first use</Badge>}
        {!model.multilingual && <Badge tone="warning">English only</Badge>}
      </div>
      <p className="hint mb-3 line-clamp-2">{model.note}</p>
      <div className="mb-3 flex gap-4">
        <Rating label="Accuracy" value={model.quality} />
        <Rating label="Speed" value={model.speed} />
      </div>
      <div className="flex items-center gap-3 text-2xs text-faint">
        <span>{model.params} params</span>
        <span aria-hidden="true">·</span>
        <span>{model.download_mb} MB download</span>
        <span aria-hidden="true">·</span>
        <span className={model.fits_gpu ? "text-positive" : "text-warning"}>
          {model.vram_fp16_mb} MB VRAM
        </span>
      </div>
    </button>
  );
}

function Rating({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-2xs text-faint">{label}</span>
      <span className="flex gap-0.5" aria-label={`${value} out of 5`}>
        {[1, 2, 3, 4, 5].map((step) => (
          <span
            key={step}
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: step <= value ? "rgb(var(--accent))" : "rgb(var(--line))",
            }}
          />
        ))}
      </span>
    </div>
  );
}
