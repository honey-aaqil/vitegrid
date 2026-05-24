/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d10",
        panel: "#14181d",
        elevated: "#1b2026",
        line: "#262c34",
        ink: "#e6e8eb",
        muted: "#8a93a0",
        accent: "#5b8def",
        "accent-dim": "#3b6bd8",
        danger: "#ef4444",
        success: "#22c55e",
      },
    },
  },
  plugins: [],
};
