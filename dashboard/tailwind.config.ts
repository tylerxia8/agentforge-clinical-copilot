import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Slate-on-white clinical palette. Designed to be readable
        // under fluorescent exam-room lighting.
        clinical: {
          bg:        "#f8fafc",
          surface:   "#ffffff",
          border:    "#e2e8f0",
          text:      "#0f172a",
          muted:     "#64748b",
          accent:    "#2563eb",
          warning:   "#d97706",
          danger:    "#dc2626",
          success:   "#15803d",
        },
      },
    },
  },
  plugins: [],
};

export default config;
