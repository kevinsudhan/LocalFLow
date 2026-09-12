/** Inline 16px icons. No icon font, no network request, no dependency. */

export type IconName =
  | "home"
  | "history"
  | "book"
  | "snippet"
  | "chip"
  | "sliders"
  | "shield"
  | "pulse"
  | "info"
  | "mic"
  | "plus"
  | "search"
  | "trash"
  | "copy"
  | "check"
  | "close"
  | "download"
  | "refresh"
  | "play"
  | "edit"
  | "external"
  | "warning"
  | "keyboard"
  | "sparkle"
  | "folder";

const PATHS: Record<IconName, JSX.Element> = {
  home: <path d="M2.5 6.8 8 2.5l5.5 4.3V13a.5.5 0 0 1-.5.5H3a.5.5 0 0 1-.5-.5V6.8Z" />,
  history: (
    <>
      <path d="M2.6 8a5.4 5.4 0 1 0 1.6-3.8" />
      <path d="M2.2 2.8v2.6h2.6" />
      <path d="M8 5.2V8l1.9 1.2" />
    </>
  ),
  book: (
    <>
      <path d="M3 2.8h4.2c.7 0 1.3.6 1.3 1.3v9c0-.7-.6-1.3-1.3-1.3H3V2.8Z" />
      <path d="M13 2.8H8.8c-.7 0-1.3.6-1.3 1.3v9c0-.7.6-1.3 1.3-1.3H13V2.8Z" />
    </>
  ),
  snippet: (
    <>
      <path d="M5.6 3.2 3 8l2.6 4.8" />
      <path d="M10.4 3.2 13 8l-2.6 4.8" />
      <path d="M9.2 2.4 6.8 13.6" />
    </>
  ),
  chip: (
    <>
      <rect x="4.2" y="4.2" width="7.6" height="7.6" rx="1.4" />
      <path d="M6.6 2v2.2M9.4 2v2.2M6.6 11.8V14M9.4 11.8V14M2 6.6h2.2M2 9.4h2.2M11.8 6.6H14M11.8 9.4H14" />
    </>
  ),
  sliders: (
    <>
      <path d="M2.6 4.6h10.8M2.6 8h10.8M2.6 11.4h10.8" />
      <circle cx="5.6" cy="4.6" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="10.4" cy="8" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="6.8" cy="11.4" r="1.5" fill="currentColor" stroke="none" />
    </>
  ),
  shield: <path d="M8 2.2 3.4 4v4c0 3 2 5 4.6 5.8C10.6 13 12.6 11 12.6 8V4L8 2.2Z" />,
  pulse: <path d="M1.8 8h2.6l1.7-4.4L8.8 12l1.6-4h3.8" />,
  info: (
    <>
      <circle cx="8" cy="8" r="5.8" />
      <path d="M8 7.2v3.4" />
      <circle cx="8" cy="5.3" r="0.85" fill="currentColor" stroke="none" />
    </>
  ),
  mic: (
    <>
      <rect x="6" y="2" width="4" height="7" rx="2" />
      <path d="M3.6 7.4a4.4 4.4 0 0 0 8.8 0" />
      <path d="M8 11.8V14" />
    </>
  ),
  plus: <path d="M8 3.4v9.2M3.4 8h9.2" />,
  search: (
    <>
      <circle cx="7.2" cy="7.2" r="4.2" />
      <path d="m10.4 10.4 3 3" />
    </>
  ),
  trash: (
    <>
      <path d="M2.8 4.4h10.4" />
      <path d="M6.4 4.4V3.2c0-.4.4-.8.8-.8h1.6c.4 0 .8.4.8.8v1.2" />
      <path d="M4.2 4.4 4.8 13c0 .4.4.7.8.7h4.8c.4 0 .8-.3.8-.7l.6-8.6" />
    </>
  ),
  copy: (
    <>
      <rect x="5.4" y="5.4" width="8" height="8" rx="1.4" />
      <path d="M10.6 5.4V3.9c0-.8-.6-1.4-1.4-1.4H4c-.8 0-1.4.6-1.4 1.4v5.2c0 .8.6 1.4 1.4 1.4h1.4" />
    </>
  ),
  check: <path d="M3.2 8.4 6.4 11.6 12.8 4.8" />,
  close: <path d="M4 4l8 8M12 4l-8 8" />,
  download: (
    <>
      <path d="M8 2.6v7.2" />
      <path d="m5 7 3 3 3-3" />
      <path d="M2.8 12.6h10.4" />
    </>
  ),
  refresh: (
    <>
      <path d="M13.2 8a5.2 5.2 0 1 1-1.5-3.7" />
      <path d="M13.4 2.6v3h-3" />
    </>
  ),
  play: <path d="M5.6 3.6 12 8l-6.4 4.4V3.6Z" fill="currentColor" stroke="none" />,
  edit: (
    <>
      <path d="M9.6 3.2 12.8 6.4 6 13.2H2.8v-3.2L9.6 3.2Z" />
      <path d="M8.4 4.4 11.6 7.6" />
    </>
  ),
  external: (
    <>
      <path d="M9.6 2.6h3.8v3.8" />
      <path d="M13.4 2.6 7.6 8.4" />
      <path d="M11.4 9.2v3a1.2 1.2 0 0 1-1.2 1.2H3.8a1.2 1.2 0 0 1-1.2-1.2V5.8a1.2 1.2 0 0 1 1.2-1.2h3" />
    </>
  ),
  warning: (
    <>
      <path d="M8 2.6 14 13H2L8 2.6Z" />
      <path d="M8 6.6v3" />
      <circle cx="8" cy="11.3" r="0.8" fill="currentColor" stroke="none" />
    </>
  ),
  keyboard: (
    <>
      <rect x="1.8" y="4" width="12.4" height="8" rx="1.6" />
      <path d="M4.4 6.6h.01M6.8 6.6h.01M9.2 6.6h.01M11.6 6.6h.01M4.4 9.4h7.2" />
    </>
  ),
  sparkle: (
    <>
      <path d="M8 2.2 9.3 6 13 7.3 9.3 8.6 8 12.4 6.7 8.6 3 7.3 6.7 6 8 2.2Z" />
      <path d="M12.6 11.4 13.1 12.9 14.6 13.4 13.1 13.9 12.6 15.4 12.1 13.9 10.6 13.4 12.1 12.9 12.6 11.4Z" />
    </>
  ),
  folder: (
    <path d="M2.4 4.4c0-.7.5-1.2 1.2-1.2h2.2l1.4 1.6h5.2c.7 0 1.2.5 1.2 1.2v5.8c0 .7-.5 1.2-1.2 1.2H3.6c-.7 0-1.2-.5-1.2-1.2V4.4Z" />
  ),
};

export function Icon({
  name,
  size = 16,
  className = "",
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
