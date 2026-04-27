import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Recall design tokens — locked at v0.4.0.
        // Mirrors src/styles/globals.css custom properties so we can use
        // either Tailwind class names or raw `--token` references.
        bg: {
          DEFAULT: "#0e0f12",
          panel: "#15171b",
          hover: "#1c1f25",
          input: "#13151a",
        },
        border: {
          DEFAULT: "#23262d",
          strong: "#2c2f37",
        },
        fg: {
          DEFAULT: "#e6e6e6",
          muted: "#8a8f98",
          dim: "#62666e",
        },
        accent: {
          DEFAULT: "#7b8cf0",
          soft: "rgba(123,140,240,0.14)",
        },
        success: "#4ade80",
        warn: "#f59e0b",
        danger: "#ef4444",
      },
      borderRadius: {
        chip: "4px",
        panel: "6px",
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "SF Pro Text",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SF Mono",
          "Menlo",
          "monospace",
        ],
      },
      boxShadow: {
        floating: "0 8px 24px rgba(0,0,0,0.45)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(2px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 200ms ease-out",
        shimmer: "shimmer 1.4s linear infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
