/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Every colour is a CSS variable so the light/dark/high-contrast
        // themes swap by changing tokens, not by duplicating classes.
        ink: "rgb(var(--ink) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        raised: "rgb(var(--raised) / <alpha-value>)",
        sunken: "rgb(var(--sunken) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-soft": "rgb(var(--accent-soft) / <alpha-value>)",
        positive: "rgb(var(--positive) / <alpha-value>)",
        warning: "rgb(var(--warning) / <alpha-value>)",
        danger: "rgb(var(--danger) / <alpha-value>)",
      },
      fontFamily: {
        sans: [
          '"Segoe UI Variable Text"',
          '"Segoe UI"',
          "Inter",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        display: [
          '"Segoe UI Variable Display"',
          '"Segoe UI"',
          "Inter",
          "system-ui",
          "sans-serif",
        ],
        mono: ['"Cascadia Code"', '"JetBrains Mono"', "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.01em" }],
      },
      borderRadius: { xl: "0.75rem", "2xl": "1rem", "3xl": "1.375rem" },
      boxShadow: {
        card: "0 1px 2px rgb(0 0 0 / 0.16), 0 0 0 1px rgb(var(--line) / 0.6)",
        lift: "0 8px 28px -12px rgb(0 0 0 / 0.55), 0 0 0 1px rgb(var(--line) / 0.7)",
        hud: "0 24px 60px -20px rgb(0 0 0 / 0.65), 0 0 0 1px rgb(255 255 255 / 0.07)",
      },
      transitionTimingFunction: {
        swift: "cubic-bezier(0.22, 0.61, 0.36, 1)",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "pulse-ring": {
          "0%": { opacity: "0.55", transform: "scale(0.92)" },
          "70%": { opacity: "0", transform: "scale(1.5)" },
          "100%": { opacity: "0" },
        },
      },
      animation: {
        "fade-up": "fade-up 220ms cubic-bezier(0.22, 0.61, 0.36, 1)",
        shimmer: "shimmer 1.8s linear infinite",
        "pulse-ring": "pulse-ring 1.8s cubic-bezier(0.22, 0.61, 0.36, 1) infinite",
      },
    },
  },
  plugins: [],
};
