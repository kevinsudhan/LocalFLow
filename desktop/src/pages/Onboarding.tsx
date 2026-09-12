import { AnimatePresence, motion } from "framer-motion";
import { Fragment, useEffect, useRef, useState } from "react";

import { Button, Meter, Select, Spinner, StatusDot } from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api, BridgeError, on, onBackend, type HotkeyProbe } from "../services/bridge";
import { useAppStore } from "../stores/appStore";

type StepId = "welcome" | "microphone" | "hardware" | "ai" | "shortcut" | "try" | "done";

const STEPS: StepId[] = ["welcome", "microphone", "hardware", "ai", "shortcut", "try", "done"];
const EASE = [0.22, 0.61, 0.36, 1] as const;

/**
 * First-run setup.
 *
 * The goal is that somebody who has never heard of Whisper, CUDA or Ollama ends
 * up with a working dictation shortcut. Every step checks something real and
 * says what it found; nothing here is decorative.
 */
export function Onboarding({ onDone }: { onDone(): void }) {
  const [index, setIndex] = useState(0);
  const step = STEPS[index];
  const next = () => setIndex((i) => Math.min(i + 1, STEPS.length - 1));
  const back = () => setIndex((i) => Math.max(i - 1, 0));

  return (
    <motion.div
      className="fixed inset-0 z-40 flex items-center justify-center bg-surface p-8"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <div className="flex w-full max-w-[620px] flex-col" style={{ minHeight: 470 }}>
        <div className="mb-6 flex items-center gap-1.5" role="progressbar" aria-valuenow={index + 1} aria-valuemax={STEPS.length}>
          {STEPS.map((id, i) => (
            <div
              key={id}
              className="h-1 flex-1 rounded-full transition-colors duration-300"
              style={{
                background:
                  i <= index ? "rgb(var(--accent))" : "rgb(var(--line))",
              }}
            />
          ))}
        </div>

        <div className="nm-raised flex flex-1 flex-col rounded-2xl p-8">
          <AnimatePresence mode="wait">
            <motion.div
              key={step}
              initial={{ opacity: 0, x: 16 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -16 }}
              transition={{ duration: 0.22, ease: EASE }}
              className="flex flex-1 flex-col"
            >
              {step === "welcome" && <Welcome />}
              {step === "microphone" && <MicrophoneStep />}
              {step === "hardware" && <HardwareStep />}
              {step === "ai" && <AiStep />}
              {step === "shortcut" && <ShortcutStep />}
              {step === "try" && <TryStep />}
              {step === "done" && <Done />}
            </motion.div>
          </AnimatePresence>

          <div className="mt-7 flex items-center justify-between">
            <Button variant="quiet" onClick={index === 0 ? onDone : back}>
              {index === 0 ? "Skip setup" : "Back"}
            </Button>
            <Button variant="primary" onClick={step === "done" ? onDone : next}>
              {step === "done" ? "Start dictating" : step === "welcome" ? "Get started" : "Continue"}
            </Button>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function Title({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-6">
      <h2 className="text-[20px] font-semibold tracking-tight text-ink">{title}</h2>
      <p className="mt-1.5 text-[13px] leading-relaxed text-muted">{description}</p>
    </div>
  );
}

function Welcome() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center text-center">
      <div className="nm-raised mb-6 flex h-20 w-20 items-center justify-center rounded-3xl">
        <svg width="38" height="38" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          {[
            { x: 3.2, h: 6 },
            { x: 7.6, h: 11 },
            { x: 12, h: 17 },
            { x: 16.4, h: 12 },
            { x: 20.8, h: 7 },
          ].map((bar, i) => (
            <motion.rect
              key={i}
              x={bar.x - 1.1}
              width="2.2"
              rx="1.1"
              fill="rgb(var(--accent))"
              opacity={0.55 + i * 0.11}
              initial={{ height: 4, y: 10 }}
              animate={{ height: bar.h, y: 12 - bar.h / 2 }}
              transition={{ delay: i * 0.06, duration: 0.4, ease: EASE }}
            />
          ))}
        </svg>
      </div>
      <h1 className="text-[24px] font-semibold tracking-tight text-ink">Welcome to LocalFlow</h1>
      <p className="mt-3 max-w-md text-[13px] leading-relaxed text-muted">
        Hold a key anywhere in Windows, say what you mean, and let go. Polished text appears at your
        cursor.
      </p>
      <p className="mt-4 max-w-md text-2xs leading-relaxed text-faint">
        Your voice is transcribed on this machine and never uploaded. Setup takes about a minute.
      </p>
    </div>
  );
}

// Read-aloud prompts. Each one is chosen to exercise something LocalFlow
// actually has to get right - a spoken number, an address it must not try to
// punctuate, a question that has to end in a question mark - so that reading
// one and looking at the result is a real test rather than a vibe check.
const READ_ALOUD: { text: string; tests: string }[] = [
  {
    text: "The quick brown fox jumps over the lazy dog.",
    tests: "every letter of the alphabet",
  },
  {
    text: "Let's meet at 3:30 on Thursday to go over the Q4 numbers.",
    tests: "times, dates and shorthand",
  },
  {
    text: "Send the draft to sarah@example.com and copy me on it.",
    tests: "an address, kept verbatim",
  },
  {
    text: "I spent about 4,500 rupees on the keyboard, which was honestly too much.",
    tests: "a large number and a clause",
  },
  {
    text: "Can you push the changes to the main branch and then run the tests?",
    tests: "a question mark you never said",
  },
  {
    text: "Let's ship it on Tuesday - actually, no, make that Friday.",
    tests: "a spoken correction",
  },
];

/** A sentence to read out loud, with what it is checking. */
function ReadAloud() {
  // Start somewhere random so returning to this screen does not always show
  // the same line, which would make it easy to test one case and no others.
  const [index, setIndex] = useState(() => Math.floor(Math.random() * READ_ALOUD.length));
  const prompt = READ_ALOUD[index];

  return (
    <div className="nm-inset mb-4 rounded-2xl p-4">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <p className="text-2xs font-medium uppercase tracking-wide text-faint">Read this out loud</p>
        <button
          type="button"
          onClick={() => setIndex((i) => (i + 1) % READ_ALOUD.length)}
          className="rounded-lg text-2xs text-muted transition-colors hover:text-ink"
        >
          Try another
        </button>
      </div>
      <AnimatePresence mode="wait">
        <motion.p
          key={index}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.18, ease: EASE }}
          className="text-[15px] leading-relaxed text-ink"
        >
          &ldquo;{prompt.text}&rdquo;
        </motion.p>
      </AnimatePresence>
      <p className="mt-2 text-2xs text-faint">Checks {prompt.tests}.</p>
    </div>
  );
}

// Thresholds on the float32 sample range. "Silent" means the stream is
// delivering digital zero - a muted endpoint or a jack with nothing plugged
// into it - which is a different problem from "you have not spoken yet".
const SPEECH_PEAK = 0.02;
const SIGNAL_PEAK = 0.0015;
const SILENCE_VERDICT_MS = 4000;

function MicrophoneStep() {
  const { devices, deviceError, settings, patch, refreshDevices, rescanDevices, activeDevice } =
    useAppStore();
  const [level, setLevel] = useState(0);
  const [peak, setPeak] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [rescanning, setRescanning] = useState(false);
  const startedRef = useRef(Date.now());

  useEffect(() => {
    void refreshDevices();
    const subscription = onBackend<{ level: number; peak: number }>("audio.level", (payload) => {
      setLevel(payload.level);
      setPeak((current) => Math.max(current, payload.peak ?? payload.level));
    });
    const timer = window.setInterval(() => setElapsed(Date.now() - startedRef.current), 500);
    // The stream can move underneath us - a device that will not open falls
    // back to another one - so the "listening on" line has to be re-read
    // rather than captured once, or it reports a device we are not using.
    const poll = window.setInterval(() => void refreshDevices(), 2000);
    return () => {
      void subscription.then((off) => off());
      window.clearInterval(timer);
      window.clearInterval(poll);
    };
  }, [refreshDevices]);

  // Reset the verdict whenever the device changes, so switching gives it a
  // fresh chance rather than inheriting the previous device's silence.
  useEffect(() => {
    setPeak(0);
    setElapsed(0);
    startedRef.current = Date.now();
  }, [activeDevice.id]);

  const heard = peak > SPEECH_PEAK;
  const hasSignal = peak > SIGNAL_PEAK;
  const silent = !hasSignal && elapsed > SILENCE_VERDICT_MS && activeDevice.streaming;

  // What the user asked for, versus what is actually open.
  const selected = devices.find((d) => d.id === settings?.audio.device_id);
  const wanted = selected?.name ?? "";
  const wrongDevice =
    Boolean(activeDevice.name) &&
    Boolean(wanted) &&
    !activeDevice.name.startsWith(wanted.slice(0, 20));

  const status = heard
    ? { title: "Microphone is working.", detail: "" }
    : activeDevice.isAlias
      ? {
          title: `LocalFlow is on “${activeDevice.name}”, which is not a microphone.`,
          detail:
            "That is a Windows audio router - it records from whatever Windows " +
            "considers the default, which is often nothing at all. Rescan, then " +
            "pick a named microphone above.",
        }
      : wrongDevice
        ? {
            title: `LocalFlow could not open “${wanted}”.`,
            detail: `It is recording from “${activeDevice.name}” instead. Rescan devices to try again.`,
          }
        : hasSignal
          ? { title: "Picking up sound - try speaking a little louder.", detail: "" }
          : silent
            ? {
                title: "This microphone is not picking anything up.",
                detail:
                  "It is delivering silence, which usually means it is muted in Windows, " +
                  "or nothing is plugged into that jack. Try another device above, or check " +
                  "the Windows volume mixer.",
              }
            : { title: "Waiting to hear you…", detail: "" };

  const problem = activeDevice.isAlias || wrongDevice || silent;

  const choose = (value: string) => {
    const device = devices.find((d) => String(d.id) === value);
    setPeak(0);
    setElapsed(0);
    startedRef.current = Date.now();
    void patch({
      audio: {
        device_id: value === "" ? null : Number(value),
        device_name: device?.name ?? "",
      },
      // The stream is reopened by the settings change; re-read which device
      // it landed on rather than assuming it honoured the request.
    }).finally(() => void refreshDevices());
  };

  const rescan = () => {
    setRescanning(true);
    setPeak(0);
    setElapsed(0);
    startedRef.current = Date.now();
    void rescanDevices().finally(() => setRescanning(false));
  };

  return (
    <div>
      <Title
        title="Check your microphone"
        description="Say something out loud - the bar should move as you speak."
      />
      {deviceError && <p className="mb-3 text-2xs text-danger">{deviceError}</p>}
      {/* One entry per physical microphone. Windows lists each one once per
          audio driver, which is noise nobody can act on during setup. */}
      {devices.length > 1 ? (
        <Select
          className="mb-3"
          aria-label="Microphone"
          value={settings?.audio.device_id === null ? "" : String(settings?.audio.device_id)}
          onChange={(e) => choose(e.target.value)}
        >
          <option value="">System default</option>
          {devices.map((device) => (
            <option key={device.id} value={device.id}>
              {device.name}
              {device.is_default ? " (default)" : ""}
            </option>
          ))}
        </Select>
      ) : (
        <p className="mb-3 flex items-center gap-2 text-[13px] text-muted">
          <Icon name="mic" className="text-faint" />
          {devices[0]?.name ?? "System default microphone"}
        </p>
      )}

      <div className="mb-5 flex items-center gap-2">
        <Button variant="quiet" onClick={rescan} disabled={rescanning} icon={<Icon name="refresh" />}>
          {rescanning ? "Rescanning…" : "Rescan devices"}
        </Button>
        <span className="text-2xs text-faint">
          Plugged something in just now? Windows only tells LocalFlow once.
        </span>
      </div>

      <ReadAloud />

      <div className="well p-5">
        <Meter value={Math.min(1, level * 7)} tone={heard ? "positive" : "accent"} />
        <div className="mt-3 flex items-start gap-2">
          <span className="mt-0.5">
            <StatusDot tone={heard ? "positive" : problem ? "warning" : "neutral"} />
          </span>
          <div className="min-w-0">
            <p className="text-2xs text-muted">{status.title}</p>
            {status.detail && <p className="mt-1 text-2xs text-faint">{status.detail}</p>}
          </div>
        </div>
        {activeDevice.name && (
          <p className="mt-3 border-t border-line/50 pt-2.5 text-2xs text-faint">
            Listening on{" "}
            <span className={problem ? "text-warning" : "text-muted"}>{activeDevice.name}</span>
            {activeDevice.hostApi && <span className="ml-1.5">· {activeDevice.hostApi}</span>}
            {peak > 0 && (
              <span className="ml-2 font-mono tabular-nums">peak {(peak * 100).toFixed(1)}%</span>
            )}
          </p>
        )}
      </div>
    </div>
  );
}

function HardwareStep() {
  const { hardware, asr, settings, refreshHardware, refreshModels, patch } = useAppStore();
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    void refreshHardware();
    void refreshModels();
  }, [refreshHardware, refreshModels]);

  // A first download is 1.6 GB and the load that follows is not instant. Keep
  // re-reading while the engine is not ready, so a long wait visibly resolves
  // into "ready" or into an error - rather than sitting on one spinner with no
  // way to tell the difference.
  useEffect(() => {
    if (asr?.ready) return undefined;
    const timer = window.setInterval(() => void refreshModels(), 2000);
    return () => window.clearInterval(timer);
  }, [asr?.ready, refreshModels]);

  const recommendation = hardware?.recommendation;
  const inUse = Boolean(recommendation && settings?.asr.model === recommendation.model);
  const settled = inUse && Boolean(asr?.ready) && !asr?.error;

  const apply = () => {
    if (!recommendation) return;
    setApplying(true);
    void patch({
      asr: { model: recommendation.model, device: "auto", compute_type: "auto" },
    })
      .catch(() => undefined)
      .finally(() => {
        setApplying(false);
        void refreshModels();
      });
  };

  return (
    <div>
      <Title
        title="Choose a speech model"
        description="LocalFlow checked your hardware and picked a model that fits."
      />

      <div className="well mb-4 p-5">
        <div className="mb-3 flex items-center gap-2.5">
          <StatusDot tone={hardware?.cuda_available ? "positive" : "warning"} />
          <span className="text-[13px] font-medium text-ink">
            {hardware?.gpu_name
              ? `${hardware.gpu_name} · ${(hardware.vram_total_mb / 1024).toFixed(0)} GB VRAM`
              : "No CUDA GPU detected"}
          </span>
        </div>
        <p className="text-2xs leading-relaxed text-muted">
          {hardware?.cuda_available
            ? "Transcription will run on the GPU, which is several times faster than the CPU."
            : "Transcription will run on the CPU. It still works well - expect a slightly longer pause after you finish speaking."}
        </p>
      </div>

      {recommendation && (
        <div className="nm-raised-sm rounded-xl p-4">
          <div className="mb-1.5 flex items-center gap-2">
            <Icon name="sparkle" className="text-accent" size={14} />
            <span className="text-[13px] font-medium text-ink">
              Recommended: {recommendation.model} on {recommendation.device.toUpperCase()}
            </span>
          </div>
          <p className="hint">{recommendation.reason}</p>
          <Button
            className="mt-3"
            // Nothing to do once it is the running model: a button that looks
            // live but changes nothing reads as a broken button.
            disabled={applying || settled}
            icon={
              applying ? <Spinner size={12} /> : settled ? <Icon name="check" size={13} /> : undefined
            }
            onClick={apply}
          >
            {applying ? "Switching…" : settled ? "In use" : "Use this"}
          </Button>
        </div>
      )}

      <div className="mt-4 flex items-start gap-2 text-2xs">
        {asr?.error ? (
          <>
            <span className="mt-0.5">
              <StatusDot tone="warning" />
            </span>
            <span className="min-w-0 break-words text-danger">
              {asr.label} could not be loaded. {asr.error}
            </span>
          </>
        ) : asr?.ready ? (
          <>
            <StatusDot tone="positive" />
            <span className="text-faint">{asr.label} is loaded and ready.</span>
          </>
        ) : (
          <>
            <Spinner size={12} />
            <span className="text-faint">
              {asr?.loading
                ? `Preparing ${asr.label || "the speech model"}…`
                : "The model downloads once, then works offline forever."}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function AiStep() {
  const { llm, settings, patch, refreshLlm, pull, toast } = useAppStore();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void refreshLlm();
  }, [refreshLlm]);

  const suggestion = llm?.suggested.find((s) => s.recommended);
  const hasModel = Boolean(settings?.llm.model);

  return (
    <div>
      <Title
        title="Optional: AI cleanup"
        description="A small local model fixes grammar and resolves spoken corrections. LocalFlow works well without it - it only steps in when the fast path is not enough."
      />

      {!llm?.available ? (
        <div className="well p-5">
          <div className="mb-2 flex items-center gap-2.5">
            <StatusDot tone="warning" />
            <span className="text-[13px] font-medium text-ink">Ollama is not running</span>
          </div>
          <p className="text-2xs leading-relaxed text-muted">
            Install Ollama from ollama.com and LocalFlow will find it automatically. You can skip
            this and everything else still works.
          </p>
          <Button className="mt-3" onClick={() => void refreshLlm()} icon={<Icon name="refresh" />}>
            Check again
          </Button>
        </div>
      ) : (
        <div className="well p-5">
          <div className="mb-3 flex items-center gap-2.5">
            <StatusDot tone={hasModel ? "positive" : "warning"} />
            <span className="text-[13px] font-medium text-ink">
              {hasModel ? `Using ${settings?.llm.model}` : "No model selected yet"}
            </span>
          </div>

          {llm.models.length > 0 && (
            <Select
              aria-label="Language model"
              value={settings?.llm.model ?? ""}
              onChange={(e) => void patch({ llm: { model: e.target.value } })}
            >
              <option value="">None - deterministic only</option>
              {llm.models.map((model) => (
                <option key={model.name} value={model.name}>
                  {model.name} ({model.size_gb} GB)
                </option>
              ))}
            </Select>
          )}

          {suggestion && !llm.models.some((m) => m.name === suggestion.name) && (
            <div className="mt-4">
              <p className="hint mb-2">
                Recommended for your hardware: <strong>{suggestion.name}</strong> ({suggestion.size_gb}{" "}
                GB) - {suggestion.note}
              </p>
              {pull ? (
                <>
                  <Meter value={pull.percent / 100} />
                  <p className="hint mt-1.5">
                    Downloading {pull.model} · {pull.percent.toFixed(0)}%
                  </p>
                </>
              ) : (
                <Button
                  busy={busy}
                  icon={<Icon name="download" />}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await api.llmPull(suggestion.name);
                    } catch (error) {
                      toast(
                        "error",
                        "Download failed",
                        error instanceof BridgeError ? error.message : String(error),
                      );
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Download {suggestion.name}
                </Button>
              )}
            </div>
          )}

          <p className="mt-4 text-2xs text-faint">
            You can use any model from the Ollama library later on the Models page.
          </p>
        </div>
      )}
    </div>
  );
}

function keysOf(shortcut: string): string[] {
  return shortcut.split("+").map((k) => k.trim()).filter(Boolean);
}

/** Live shortcut tester.
 *
 * The keyboard hook is global and invisible, so a shortcut that does not fire
 * is indistinguishable from one that does nothing - which is the single most
 * confusing failure this app has. This reacts the instant the chord is seen,
 * and if nothing arrives it asks the hook what it is actually observing.
 */
function ShortcutTester({ shortcut }: { shortcut: string }) {
  const [held, setHeld] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [lastHold, setLastHold] = useState<number | null>(null);
  const [probe, setProbe] = useState<HotkeyProbe | null>(null);
  const [webviewSaw, setWebviewSaw] = useState(false);
  const downAt = useRef(0);
  const openedAt = useRef(Date.now());

  useEffect(() => {
    const subscription = on<{ event: string }>("localflow:hotkey", (payload) => {
      if (payload.event === "down") {
        downAt.current = Date.now();
        setElapsed(0);
        setLastHold(null);
        setHeld(true);
      } else if (payload.event === "up") {
        setHeld(false);
        setLastHold(Date.now() - downAt.current);
      }
    });
    return () => void subscription.then((off) => off());
  }, []);

  useEffect(() => {
    if (!held) return undefined;
    const timer = window.setInterval(() => setElapsed(Date.now() - downAt.current), 60);
    return () => window.clearInterval(timer);
  }, [held]);

  // Cross-check: did the webview see the chord that the hook did not?
  //
  // A live hook swallows the combination before any window receives it, so if
  // this fires while the hook counts nothing, keys are reaching the process but
  // not the hook - a completely different fault from keys never arriving.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const parts = keysOf(shortcut);
      const wanted = (parts[parts.length - 1] ?? "").toLowerCase();
      const pressed = event.key === " " ? "space" : event.key.toLowerCase();
      if (pressed !== wanted) return;
      const mods = parts.slice(0, -1).map((m) => m.toLowerCase());
      if (mods.includes("ctrl") !== event.ctrlKey) return;
      if (mods.includes("shift") !== event.shiftKey) return;
      if (mods.includes("alt") !== event.altKey) return;
      setWebviewSaw(true);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [shortcut]);

  // Only interrogate the hook once the user has plausibly tried: a diagnosis
  // offered before anyone has pressed anything is just noise.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (Date.now() - openedAt.current < 6000) return;
      void api.hotkeyProbe().then(setProbe).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, []);

  const keys = keysOf(shortcut);
  const key = keys[keys.length - 1] ?? "";
  const modifiers = keys.slice(0, -1).join(" + ");
  const working = lastHold !== null || (probe?.matches ?? 0) > 0;

  const trouble =
    working || held || !probe
      ? ""
      : !probe.installed
        ? "Windows has not granted LocalFlow a keyboard hook, so no shortcut can work. Restarting LocalFlow usually fixes it."
        : !probe.enabled
          ? "The shortcut is switched off right now - check that dictation is not paused in the tray."
          : probe.key_without_chord > 0
            ? `${key} is reaching LocalFlow, but without ${modifiers}. Hold ${modifiers} down first, then press ${key}.`
            : webviewSaw
              ? `LocalFlow's window received ${shortcut}, but the system-wide keyboard hook did not. Windows has stopped delivering keys to the hook - restarting LocalFlow reinstalls it.`
              : "No key presses are reaching LocalFlow at all. Another application may be claiming this combination - try one of the alternatives below.";

  return (
    <div className="well relative mt-4 overflow-hidden p-6">
      <AnimatePresence>
        {held && (
          <motion.div
            key="glow"
            initial={{ opacity: 0, scale: 0.7 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 1.3 }}
            transition={{ duration: 0.28, ease: EASE }}
            className="pointer-events-none absolute left-1/2 top-[42%] h-48 w-48 -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              background: "radial-gradient(circle, rgb(var(--accent) / 0.24), transparent 70%)",
            }}
          />
        )}
      </AnimatePresence>

      {held &&
        [0, 1].map((ring) => (
          <motion.span
            key={`ring-${ring}`}
            aria-hidden="true"
            className="pointer-events-none absolute left-1/2 top-[42%] h-24 w-24 -translate-x-1/2 -translate-y-1/2 rounded-full border"
            style={{ borderColor: "rgb(var(--accent) / 0.4)" }}
            initial={{ scale: 0.55, opacity: 0.55 }}
            animate={{ scale: 2.3, opacity: 0 }}
            transition={{ duration: 1.7, repeat: Infinity, delay: ring * 0.85, ease: "easeOut" }}
          />
        ))}

      <div className="relative flex items-center justify-center gap-2.5">
        {keys.map((cap, index) => (
          <Fragment key={`${cap}-${index}`}>
            {index > 0 && <span className="text-[15px] text-faint">+</span>}
            <motion.span
              animate={held ? { y: 3, scale: 0.96 } : { y: 0, scale: 1 }}
              transition={{ type: "spring", stiffness: 540, damping: 28 }}
              className={`inline-flex min-w-[58px] items-center justify-center rounded-xl px-4 py-3
                          font-mono text-[15px] font-semibold transition-colors duration-150
                          ${held ? "nm-inset text-accent" : "nm-raised-sm text-ink"}`}
            >
              {cap}
            </motion.span>
          </Fragment>
        ))}
      </div>

      <div className="relative mt-5 text-center">
        {held ? (
          <p className="text-[13px] font-medium text-accent">
            Reading your shortcut…{" "}
            <span className="font-mono tabular-nums">{(elapsed / 1000).toFixed(1)}s</span>
          </p>
        ) : lastHold !== null ? (
          <p className="flex items-center justify-center gap-1.5 text-[13px] font-medium text-positive">
            <Icon name="check" size={13} />
            Shortcut detected - held for {(lastHold / 1000).toFixed(1)}s
          </p>
        ) : (
          <p className="text-[13px] text-muted">
            Hold <span className="font-medium text-ink">{shortcut}</span> now to test it
          </p>
        )}
        {trouble && (
          <p className="mx-auto mt-2.5 max-w-sm text-2xs leading-relaxed text-warning">{trouble}</p>
        )}
        {probe && !working && (
          <p className="mt-2 font-mono text-[10px] tracking-tight text-faint">
            hook {probe.installed ? "installed" : "missing"} ·{" "}
            {probe.enabled ? "enabled" : "disabled"} · {probe.primary || "unset"} ·{" "}
            {probe.matches} matched · {probe.key_without_chord} partial ·{" "}
            {webviewSaw ? "window saw it" : "window saw nothing"}
          </p>
        )}
      </div>
    </div>
  );
}

function ShortcutStep() {
  const { settings, patch, toast } = useAppStore();
  const [capturing, setCapturing] = useState(false);

  useEffect(() => {
    if (!capturing) return;
    const onKeyDown = async (event: KeyboardEvent) => {
      event.preventDefault();
      if (["Control", "Shift", "Alt", "Meta"].includes(event.key)) return;
      if (event.key === "Escape") {
        setCapturing(false);
        return;
      }
      const parts: string[] = [];
      if (event.ctrlKey) parts.push("Ctrl");
      if (event.shiftKey) parts.push("Shift");
      if (event.altKey) parts.push("Alt");
      if (event.metaKey) parts.push("Win");
      parts.push(
        event.key === " " ? "Space" : event.key.length === 1 ? event.key.toUpperCase() : event.key,
      );
      try {
        const accepted = await api.validateHotkey(parts.join("+"));
        await patch({ hotkeys: { primary: accepted } });
      } catch (error) {
        toast(
          "error",
          "That shortcut will not work",
          error instanceof BridgeError ? error.message : String(error),
        );
      } finally {
        setCapturing(false);
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [capturing, patch, toast]);

  return (
    <div>
      <Title
        title="Pick your shortcut"
        description="Hold it anywhere in Windows to dictate. LocalFlow intercepts only this exact combination."
      />

      <button
        type="button"
        onClick={() => setCapturing(true)}
        className={`w-full rounded-2xl px-6 py-9 text-center transition-all duration-200
                    ${capturing ? "nm-inset" : "nm-raised"}`}
      >
        <p className="mb-2 text-2xs text-faint">
          {capturing ? "Press a combination now" : "Current shortcut"}
        </p>
        <p className="font-mono text-[22px] font-semibold tracking-tight text-ink">
          {capturing ? "…" : settings?.hotkeys.primary}
        </p>
        <p className="mt-2 text-2xs text-muted">Click to change</p>
      </button>

      <ShortcutTester shortcut={settings?.hotkeys.primary ?? "Ctrl+Space"} />

      <div className="mt-4 grid grid-cols-3 gap-2">
        {["Ctrl+Space", "Alt+D", "F9"].map((option) => (
          <Button
            key={option}
            data-active={settings?.hotkeys.primary === option}
            onClick={() => void patch({ hotkeys: { primary: option } })}
          >
            {option}
          </Button>
        ))}
      </div>

      <p className="hint mt-4">
        Tip: a quick tap locks recording on so you can let go of the keys and keep talking. Press
        again to finish.
      </p>
    </div>
  );
}

function TryStep() {
  const settings = useAppStore((s) => s.settings);
  const [heard, setHeard] = useState<string>("");
  const [state, setState] = useState<string>("idle");

  useEffect(() => {
    const subscriptions = [
      onBackend<{ state: string }>("session.state", (payload) => setState(payload.state)),
      onBackend<{ text: string }>("session.partial", (payload) => setHeard(payload.text)),
    ];
    const resultSub = api
      .diagnostics()
      .then(() => undefined)
      .catch(() => undefined);
    void resultSub;
    return () => subscriptions.forEach((p) => void p.then((off) => off()));
  }, []);

  return (
    <div>
      <Title
        title="Try it now"
        description="Click into the box below, then hold your shortcut and say something."
      />

      <ReadAloud />

      <textarea
        className="input mb-4 min-h-[130px] resize-none font-sans text-[14px] leading-relaxed"
        placeholder="Your dictated text will appear right here…"
        aria-label="Practice area"
      />

      <div className="well flex items-center gap-3 p-4">
        <StatusDot
          tone={state === "listening" ? "positive" : state === "processing" ? "warning" : "neutral"}
        />
        <span className="text-2xs text-muted">
          {state === "listening"
            ? heard
              ? `Heard: ${heard}`
              : "Listening…"
            : state === "processing"
              ? "Processing…"
              : `Hold ${settings?.hotkeys.primary} and speak`}
        </span>
      </div>

      <p className="hint mt-4">
        This works in any application - Notepad, Gmail, Slack, VS Code, your terminal. LocalFlow
        adapts its formatting to whichever one has your cursor.
      </p>
    </div>
  );
}

function Done() {
  const settings = useAppStore((s) => s.settings);
  return (
    <div className="flex flex-1 flex-col items-center justify-center text-center">
      <motion.div
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.3, ease: EASE }}
        className="nm-raised mb-6 flex h-16 w-16 items-center justify-center rounded-3xl text-positive"
      >
        <Icon name="check" size={28} />
      </motion.div>
      <h2 className="text-[20px] font-semibold tracking-tight text-ink">You are set up</h2>
      <p className="mt-2.5 max-w-sm text-[13px] leading-relaxed text-muted">
        Hold <kbd className="kbd mx-0.5">{settings?.hotkeys.primary}</kbd> in any application and
        start talking. LocalFlow lives in your system tray.
      </p>
      <div className="mt-6 grid w-full max-w-sm grid-cols-1 gap-2 text-left">
        {[
          ["Add your vocabulary", "Teach it names and jargon it would otherwise mishear."],
          ["Create snippets", "Say a phrase, insert a saved block of text."],
          ["Say “undo that”", "Removes what it just inserted."],
        ].map(([title, detail]) => (
          <div key={title} className="nm-raised-sm rounded-xl px-4 py-2.5">
            <p className="text-2xs font-medium text-ink">{title}</p>
            <p className="text-2xs text-muted">{detail}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
