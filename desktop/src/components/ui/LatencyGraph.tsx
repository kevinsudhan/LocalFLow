import { useId, useState } from "react";

import type { LlmPoint } from "../../types";

/**
 * Recent dictation timings, drawn as two lines on one horizontal axis.
 *
 * The question this answers is not "how fast is it on average", which a number
 * already answers better than a picture can. It is "which dictations were slow,
 * and was it the language model". Those are different lines, so they are drawn
 * as different lines: total response underneath, and the language model's share
 * of it on top. Where the two meet, the model is the whole cost.
 *
 * Dictations the model never touched sit at zero on the upper line rather than
 * being dropped, because a gap is the useful part of the picture: it is what
 * the deterministic pipeline handled on its own.
 */

const WIDTH = 520;
const HEIGHT = 96;
const PAD_TOP = 10;
const PAD_BOTTOM = 16;

export function LatencyGraph({ points }: { points: LlmPoint[] }) {
  const clipId = useId();
  const [hover, setHover] = useState<number | null>(null);

  if (points.length < 2) {
    return (
      <p className="px-5 pb-5 text-2xs text-muted">
        A few more dictations and the timings will show up here.
      </p>
    );
  }

  // One scale for both lines. Separate scales would make the language model
  // look like the whole cost on every dictation it touched.
  const ceiling = Math.max(600, ...points.map((p) => p.response_ms));
  const plot = HEIGHT - PAD_TOP - PAD_BOTTOM;
  const x = (index: number) => (index / (points.length - 1)) * WIDTH;
  const y = (ms: number) => PAD_TOP + plot - (Math.min(ms, ceiling) / ceiling) * plot;

  const line = (pick: (p: LlmPoint) => number) =>
    points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(pick(p)).toFixed(1)}`).join(" ");

  const responsePath = line((p) => p.response_ms);
  const llmPath = line((p) => p.llm_ms);
  const area = `${responsePath} L${WIDTH},${HEIGHT - PAD_BOTTOM} L0,${HEIGHT - PAD_BOTTOM} Z`;
  const active = hover === null ? null : points[hover];

  return (
    <div className="px-5 pb-4">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        role="img"
        aria-label={`Response time across the last ${points.length} dictations, with the share spent on the local language model.`}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={`${clipId}-fill`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--accent) / 0.22)" />
            <stop offset="100%" stopColor="rgb(var(--accent) / 0)" />
          </linearGradient>
        </defs>

        <line
          x1="0"
          x2={WIDTH}
          y1={HEIGHT - PAD_BOTTOM}
          y2={HEIGHT - PAD_BOTTOM}
          stroke="rgb(var(--ink) / 0.12)"
          strokeWidth="1"
        />

        <path d={area} fill={`url(#${clipId}-fill)`} />
        <path
          d={responsePath}
          fill="none"
          stroke="rgb(var(--accent))"
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <path
          d={llmPath}
          fill="none"
          stroke="rgb(var(--accent-2))"
          strokeWidth="1.5"
          strokeDasharray="3 2.5"
          strokeLinejoin="round"
          strokeLinecap="round"
          opacity="0.85"
        />

        {/* A dot per dictation the model touched but whose output was thrown
            away. That time was spent and nothing was returned for it. */}
        {points.map((p, i) =>
          p.llm_ran && !p.used_llm ? (
            <circle
              key={p.id}
              cx={x(i)}
              cy={y(p.llm_ms)}
              r="2.4"
              fill="rgb(var(--surface))"
              stroke="rgb(var(--warning))"
              strokeWidth="1.4"
            />
          ) : null,
        )}

        {/* Invisible columns so the whole width is hoverable, not just the line. */}
        {points.map((p, i) => (
          <rect
            key={`hit-${p.id}`}
            x={x(i) - WIDTH / points.length / 2}
            y="0"
            width={WIDTH / points.length}
            height={HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}

        {hover !== null && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={PAD_TOP - 4}
            y2={HEIGHT - PAD_BOTTOM}
            stroke="rgb(var(--ink) / 0.28)"
            strokeWidth="1"
          />
        )}
      </svg>

      <div className="mt-1 flex items-center justify-between text-2xs">
        <div className="flex items-center gap-3 text-faint">
          <Key tone="accent">Response</Key>
          <Key tone="dashed">AI cleanup</Key>
          <Key tone="ring">Discarded</Key>
        </div>
        <span className="font-mono tabular-nums text-muted">
          {active
            ? `${Math.round(active.response_ms)} ms` +
              (active.llm_ran
                ? `, ${Math.round(active.llm_ms)} ms of it AI${active.used_llm ? "" : ", discarded"}`
                : ", no AI")
            : `last ${points.length}`}
        </span>
      </div>
    </div>
  );
}

function Key({ tone, children }: { tone: "accent" | "dashed" | "ring"; children: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <svg width="14" height="8" aria-hidden="true">
        {tone === "ring" ? (
          <circle
            cx="7"
            cy="4"
            r="2.4"
            fill="rgb(var(--surface))"
            stroke="rgb(var(--warning))"
            strokeWidth="1.4"
          />
        ) : (
          <line
            x1="0"
            x2="14"
            y1="4"
            y2="4"
            stroke={tone === "accent" ? "rgb(var(--accent))" : "rgb(var(--accent-2))"}
            strokeWidth={tone === "accent" ? 1.8 : 1.5}
            strokeDasharray={tone === "dashed" ? "3 2.5" : undefined}
          />
        )}
      </svg>
      {children}
    </span>
  );
}
