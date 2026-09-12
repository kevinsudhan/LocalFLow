import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

import { VoiceDots } from "../components/hud/VoiceDots";
import { on } from "../services/bridge";
import type { HudPayload, HudState } from "../types";

const REST: HudPayload = { state: "idle" };
const EASE = [0.22, 0.61, 0.36, 1] as const;

/**
 * The floating dictation HUD.
 *
 * Three constraints shape everything here: it sits on top of the user's real
 * work, it must never take focus or intercept a click, and it has to be
 * readable in a glance without being looked at directly.
 *
 * So it is a single dark pill, no bigger than it needs to be. At rest it is a
 * row of dots; speaking stretches them; a partial transcript widens the pill
 * and nothing else moves. There are no controls, because a control would be
 * something to aim at.
 */
export function Hud() {
  const [payload, setPayload] = useState<HudPayload>(REST);
  const [partial, setPartial] = useState("");
  const [level, setLevel] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(false);
  const startedRef = useRef(0);

  useEffect(() => {
    document.body.dataset.window = "hud";
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(media.matches);
    const onMediaChange = () => setReducedMotion(media.matches);
    media.addEventListener("change", onMediaChange);

    const subscriptions = [
      on<HudPayload>("hud:state", (next) => {
        setPayload(next);
        if (next.state === "listening") {
          if (!startedRef.current) startedRef.current = Date.now();
        } else if (next.state === "idle") {
          startedRef.current = 0;
          setPartial("");
          setLevel(0);
          setElapsed(0);
        } else if (next.state !== "processing") {
          startedRef.current = 0;
        }
      }),
      on<HudPayload>("hud:partial", (next) => {
        if (next.text !== undefined) setPartial(next.text);
      }),
      on<{ level: number }>("hud:level", (next) => setLevel(next.level)),
    ];

    return () => {
      media.removeEventListener("change", onMediaChange);
      subscriptions.forEach((p) => void p.then((off) => off()));
    };
  }, []);

  useEffect(() => {
    if (payload.state !== "listening") return;
    const timer = window.setInterval(() => {
      if (startedRef.current) setElapsed(Date.now() - startedRef.current);
    }, 200);
    return () => window.clearInterval(timer);
  }, [payload.state]);

  const { state } = payload;
  const listening = state === "listening";
  const working = state === "processing" || state === "inserting";
  const caption = payload.message ?? (state === "error" ? "Something went wrong" : "");
  const transcript = state === "error" ? "" : payload.text || partial;
  const showTranscript = listening && Boolean(transcript);

  return (
    <div className="flex h-screen w-screen items-center justify-center">
      <AnimatePresence>
        {state !== "idle" && (
          <motion.div
            key="pill"
            layout={!reducedMotion}
            initial={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reducedMotion ? { opacity: 0 } : { opacity: 0, y: 4, scale: 0.94 }}
            transition={{ duration: reducedMotion ? 0.08 : 0.26, ease: EASE }}
            className="pointer-events-none flex items-center gap-2.5 rounded-full px-4 py-2.5"
            style={{
              background: "rgb(9 9 11 / 0.94)",
              backdropFilter: "blur(20px) saturate(150%)",
              WebkitBackdropFilter: "blur(20px) saturate(150%)",
              boxShadow:
                "0 12px 32px -8px rgb(0 0 0 / 0.6), 0 0 0 0.5px rgb(255 255 255 / 0.1) inset",
              maxWidth: "min(92vw, 460px)",
            }}
            role="status"
            aria-live="polite"
            aria-label={ariaLabel(state, transcript)}
          >
            {state === "success" ? (
              <Check reducedMotion={reducedMotion} />
            ) : state === "error" ? (
              <Alert />
            ) : (
              <VoiceDots
                level={level}
                active={listening}
                mode={listening ? "listening" : working ? "working" : "rest"}
                reducedMotion={reducedMotion}
              />
            )}

            <AnimatePresence mode="popLayout" initial={false}>
              {showTranscript && (
                <motion.span
                  key="transcript"
                  layout={!reducedMotion}
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: "auto" }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: reducedMotion ? 0.05 : 0.22, ease: EASE }}
                  className="overflow-hidden whitespace-nowrap text-[12.5px] font-normal
                             leading-none text-white/70"
                  style={{ maxWidth: 300, textOverflow: "ellipsis" }}
                >
                  {tail(transcript, 62)}
                </motion.span>
              )}

              {caption && (
                <motion.span
                  key="caption"
                  layout={!reducedMotion}
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: "auto" }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: reducedMotion ? 0.05 : 0.22, ease: EASE }}
                  className="overflow-hidden whitespace-nowrap text-[12.5px] leading-none text-white/65"
                  style={{ maxWidth: 320 }}
                >
                  {caption}
                </motion.span>
              )}

              {listening && !showTranscript && elapsed > 1200 && (
                <motion.span
                  key="timer"
                  layout={!reducedMotion}
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: "auto" }}
                  exit={{ opacity: 0, width: 0 }}
                  transition={{ duration: 0.18, ease: EASE }}
                  className="font-mono text-[11px] tabular-nums leading-none text-white/35"
                >
                  {formatDuration(elapsed)}
                </motion.span>
              )}
            </AnimatePresence>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Check({ reducedMotion }: { reducedMotion: boolean }) {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <motion.path
        d="M5 10.5L8.5 14L15 7"
        stroke="rgb(94 200 148)"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={reducedMotion ? { pathLength: 1 } : { pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: reducedMotion ? 0 : 0.26, ease: EASE }}
      />
    </svg>
  );
}

function Alert() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="10" cy="10" r="7.2" stroke="rgb(233 178 96)" strokeWidth="1.6" />
      <path
        d="M10 6.4v4.2"
        stroke="rgb(233 178 96)"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
      <circle cx="10" cy="13.6" r="0.95" fill="rgb(233 178 96)" />
    </svg>
  );
}

/** Keep the most recent words visible as the transcript grows. */
function tail(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  return "…" + clean.slice(clean.length - limit).replace(/^\S*\s/, "");
}

function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000);
  return `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, "0")}`;
}

function ariaLabel(state: HudState, transcript: string): string {
  switch (state) {
    case "listening":
      return transcript ? `Listening. ${transcript}` : "Listening";
    case "processing":
      return "Processing speech";
    case "inserting":
      return "Inserting text";
    case "success":
      return "Text inserted";
    case "error":
      return "Dictation failed";
    default:
      return "";
  }
}
