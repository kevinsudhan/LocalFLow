import { useEffect, useRef } from "react";

interface Props {
  level: number;
  active: boolean;
  /** "listening" reacts to the voice; "working" runs a travelling shimmer. */
  mode?: "listening" | "working" | "rest";
  dots?: number;
  reducedMotion?: boolean;
}

const DOT = 3.2; // resting diameter in CSS pixels
const MAX_SCALE = 4.6; // how far a dot stretches at full volume

/**
 * The voice indicator: a row of dots that stretch into capsules as you speak.
 *
 * At rest they are perfect circles, which is why the HUD reads as a quiet pill
 * rather than a piece of UI. Loudness stretches each dot vertically only, so
 * the row keeps its width and the pill never reflows while someone is talking.
 *
 * Painted on canvas because this repaints ~40 times a second for the entire
 * duration of every dictation, directly over whatever the user is working in.
 * Animating DOM nodes here would keep the compositor busy for no visual gain.
 */
export function VoiceDots({
  level,
  active,
  mode = "listening",
  dots = 9,
  reducedMotion = false,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const historyRef = useRef<number[]>(new Array(dots).fill(0));
  const levelRef = useRef(0);
  const frameRef = useRef(0);
  const lastPushRef = useRef(0);
  const phaseRef = useRef(0);

  levelRef.current = level;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      canvas.width = Math.max(1, Math.round(canvas.clientWidth * dpr));
      canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    };
    resize();

    const render = (timestamp: number) => {
      frameRef.current = requestAnimationFrame(render);

      const history = historyRef.current;
      if (timestamp - lastPushRef.current > 42) {
        lastPushRef.current = timestamp;
        if (mode === "listening" && active) {
          // Speech RMS is small and highly peaked; the exponent lifts quiet
          // speech into view without letting loud speech clip the row.
          const shaped = Math.min(1, Math.pow(Math.max(0, levelRef.current) * 7.2, 0.6));
          history.shift();
          history.push(shaped);
        } else if (mode === "working") {
          phaseRef.current += 0.34;
          for (let i = 0; i < dots; i += 1) {
            // A single hump travelling along the row: unmistakably "busy"
            // without the jitter of a spinner.
            const distance = Math.abs(((phaseRef.current % (dots + 3)) - i) / 1.7);
            history[i] = Math.max(0, 1 - distance * distance) * 0.55;
          }
        } else {
          for (let i = 0; i < dots; i += 1) history[i] *= 0.82;
        }
      }

      const width = canvas.width;
      const height = canvas.height;
      context.clearRect(0, 0, width, height);

      const dotSize = DOT * dpr;
      const gap = 3.4 * dpr;
      const totalWidth = dots * dotSize + (dots - 1) * gap;
      const startX = (width - totalWidth) / 2;
      const centre = height / 2;

      for (let i = 0; i < dots; i += 1) {
        const value = history[i] ?? 0;
        const barHeight = dotSize * (1 + value * MAX_SCALE);
        const x = startX + i * (dotSize + gap);
        const y = centre - barHeight / 2;
        const alpha = mode === "rest" ? 0.32 : 0.5 + value * 0.5;
        context.fillStyle = `rgba(255, 255, 255, ${alpha})`;
        context.beginPath();
        context.roundRect(x, y, dotSize, barHeight, dotSize / 2);
        context.fill();
      }
    };

    if (reducedMotion) {
      const paint = () => {
        const value =
          mode === "listening" && active
            ? Math.min(1, Math.pow(Math.max(0, levelRef.current) * 7.2, 0.6))
            : mode === "working"
              ? 0.4
              : 0;
        historyRef.current = new Array(dots).fill(value);
        render(performance.now());
        cancelAnimationFrame(frameRef.current);
      };
      paint();
      const timer = window.setInterval(paint, 140);
      return () => {
        window.clearInterval(timer);
        cancelAnimationFrame(frameRef.current);
      };
    }

    frameRef.current = requestAnimationFrame(render);
    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(frameRef.current);
      window.removeEventListener("resize", resize);
    };
  }, [active, dots, mode, reducedMotion]);

  return <canvas ref={canvasRef} className="h-6 w-[92px]" aria-hidden="true" />;
}
