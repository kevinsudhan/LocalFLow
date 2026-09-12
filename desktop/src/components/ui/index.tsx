/** The LocalFlow control set. Neumorphic surfaces, accessible semantics. */
import { AnimatePresence, motion } from "framer-motion";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";

const EASE = [0.22, 0.61, 0.36, 1] as const;

/* ------------------------------------------------------------------ layout */

export function Card({
  children,
  className = "",
  padded = false,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <div className={`card ${padded ? "p-5" : ""} ${className}`}>{children}</div>
  );
}

export function Section({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-8">
      <header className="mb-3 flex items-end justify-between gap-4 px-1">
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
          {description && <p className="mt-0.5 max-w-2xl text-2xs text-muted">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function Row({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="row">
      <div className="min-w-0 flex-1">
        <label htmlFor={htmlFor} className="label block">
          {label}
        </label>
        {hint && <p className="hint mt-1 max-w-xl">{hint}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-7 flex items-start justify-between gap-6">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------- controls */

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "quiet" | "danger";
  busy?: boolean;
  icon?: ReactNode;
};

export function Button({
  variant = "ghost",
  busy = false,
  icon,
  children,
  className = "",
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      className={`btn btn-${variant} ${className}`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy ? <Spinner size={13} /> : icon}
      {children}
    </button>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      className="animate-spin"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="6.4" stroke="currentColor" strokeOpacity="0.22" strokeWidth="2" />
      <path
        d="M14.4 8A6.4 6.4 0 0 0 8 1.6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function Toggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange(next: boolean): void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-all duration-200 ease-swift
                  disabled:cursor-not-allowed disabled:opacity-45
                  ${checked ? "" : "nm-inset"}`}
      style={
        checked
          ? {
              background:
                "linear-gradient(145deg, rgb(var(--accent)), rgb(var(--accent) / 0.78))",
              boxShadow:
                "inset 2px 2px 5px rgb(0 0 0 / 0.28), inset -2px -2px 5px rgb(255 255 255 / 0.16)",
            }
          : undefined
      }
    >
      <motion.span
        layout
        transition={{ type: "spring", stiffness: 640, damping: 38 }}
        className="absolute top-1 h-4 w-4 rounded-full"
        style={{
          left: checked ? 26 : 4,
          background: checked ? "rgb(255 255 255)" : "rgb(var(--muted))",
          boxShadow: "1px 1px 3px rgb(var(--nm-dark) / 0.8)",
        }}
      />
    </button>
  );
}

export function TextInput({
  className = "",
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`input ${className}`} {...rest} />;
}

export function Select({
  className = "",
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        className={`input cursor-pointer appearance-none pr-9 ${className}`}
        {...rest}
      >
        {children}
      </select>
      <svg
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-faint"
        width="11"
        height="11"
        viewBox="0 0 12 12"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M2.5 4.5 6 8l3.5-3.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: Array<{ value: T; label: string; hint?: string }>;
  onChange(next: T): void;
  ariaLabel?: string;
}) {
  return (
    <div className="nm-inset inline-flex gap-1 rounded-xl p-1" role="radiogroup" aria-label={ariaLabel}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={option.hint}
            onClick={() => onChange(option.value)}
            className={`relative rounded-lg px-3 py-1.5 text-2xs font-medium transition-colors
                        duration-150 ${active ? "text-ink" : "text-muted hover:text-ink"}`}
          >
            {active && (
              <motion.span
                layoutId={`segmented-${ariaLabel ?? "group"}`}
                className="nm-raised-sm absolute inset-0 rounded-lg"
                transition={{ type: "spring", stiffness: 520, damping: 40 }}
              />
            )}
            <span className="relative z-10">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  format,
  label,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange(next: number): void;
  format?(value: number): string;
  label?: string;
}) {
  const percent = ((value - min) / (max - min)) * 100;
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-5 w-44">
        <div className="nm-inset absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full" />
        <div
          className="absolute top-1/2 h-2 -translate-y-1/2 rounded-full"
          style={{
            width: `${percent}%`,
            background: "linear-gradient(90deg, rgb(var(--accent) / 0.55), rgb(var(--accent)))",
          }}
        />
        <input
          type="range"
          aria-label={label}
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="absolute inset-0 w-full cursor-pointer appearance-none bg-transparent
                     [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4
                     [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
                     [&::-webkit-slider-thumb]:bg-white
                     [&::-webkit-slider-thumb]:shadow-[1px_1px_4px_rgb(0_0_0/0.45)]"
        />
      </div>
      <span className="w-16 shrink-0 text-right font-mono text-2xs tabular-nums text-muted">
        {format ? format(value) : value}
      </span>
    </div>
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "positive" | "warning" | "danger" | "accent";
  children: ReactNode;
}) {
  const colour = {
    neutral: "text-muted",
    positive: "text-positive",
    warning: "text-warning",
    danger: "text-danger",
    accent: "text-accent",
  }[tone];
  return <span className={`chip ${colour}`}>{children}</span>;
}

export function StatusDot({ tone }: { tone: "positive" | "warning" | "danger" | "neutral" }) {
  const colour = {
    positive: "rgb(var(--positive))",
    warning: "rgb(var(--warning))",
    danger: "rgb(var(--danger))",
    neutral: "rgb(var(--faint))",
  }[tone];
  return (
    <span
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
      style={{ background: colour, boxShadow: `0 0 7px ${colour}` }}
      aria-hidden="true"
    />
  );
}

/* -------------------------------------------------------------- structures */

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      {icon && (
        <div className="nm-inset mb-4 flex h-14 w-14 items-center justify-center rounded-2xl text-faint">
          {icon}
        </div>
      )}
      <p className="text-[14px] font-medium text-ink">{title}</p>
      {description && <p className="mt-1.5 max-w-sm text-2xs text-muted">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = 520,
}: {
  open: boolean;
  onClose(): void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    // Move focus into the dialog so Tab stays inside it.
    ref.current?.querySelector<HTMLElement>("input, textarea, button")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
        >
          <div
            className="absolute inset-0 bg-black/45 backdrop-blur-[2px]"
            onClick={onClose}
            aria-hidden="true"
          />
          <motion.div
            ref={ref}
            role="dialog"
            aria-modal="true"
            aria-label={title}
            initial={{ opacity: 0, scale: 0.97, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: 4 }}
            transition={{ duration: 0.18, ease: EASE }}
            className="nm-raised relative w-full overflow-hidden rounded-2xl"
            style={{ maxWidth: width }}
          >
            <div className="px-5 pb-1 pt-5">
              <h3 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h3>
            </div>
            <div className="px-5 py-4">{children}</div>
            {footer && (
              <div className="flex justify-end gap-2 px-5 pb-5 pt-1">{footer}</div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function ConfirmButton({
  onConfirm,
  children,
  confirmLabel = "Click again to confirm",
  variant = "danger",
}: {
  onConfirm(): void;
  children: ReactNode;
  confirmLabel?: string;
  variant?: ButtonProps["variant"];
}) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), 3500);
    return () => window.clearTimeout(timer);
  }, [armed]);

  return (
    <Button
      variant={variant}
      onClick={() => {
        if (armed) {
          onConfirm();
          setArmed(false);
        } else {
          setArmed(true);
        }
      }}
    >
      {armed ? confirmLabel : children}
    </Button>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="mb-4">
      <label htmlFor={id} className="label mb-1.5 block">
        {label}
      </label>
      {children(id)}
      {error ? (
        <p className="mt-1.5 text-2xs text-danger">{error}</p>
      ) : hint ? (
        <p className="hint mt-1.5">{hint}</p>
      ) : null}
    </div>
  );
}

export function Meter({ value, tone = "accent" }: { value: number; tone?: "accent" | "positive" }) {
  const colour = tone === "positive" ? "var(--positive)" : "var(--accent)";
  return (
    <div className="nm-inset h-2 w-full overflow-hidden rounded-full">
      <motion.div
        className="h-full rounded-full"
        style={{ background: `rgb(${colour})` }}
        animate={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }}
        transition={{ duration: 0.08, ease: "linear" }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ toasts */

interface ToastContextValue {
  push(kind: "info" | "success" | "error", title: string, detail?: string): void;
}
const ToastContext = createContext<ToastContextValue | null>(null);
export const useToast = () => useContext(ToastContext);
export { ToastContext };
