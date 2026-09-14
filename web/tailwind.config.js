/** @type {import('tailwindcss').Config} */

/**
 * The theme maps Tailwind utilities onto the tokens in `src/styles/tokens.css`.
 *
 * Everything here is *semantic* — `bg-surface`, `text-body`, `ring-critical` —
 * so a component never names a colour and dark mode needs no variant classes.
 * The raw `slate-*` scale is still available where a one-off genuinely needs
 * it, but reaching for it is a signal the token set is missing something.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // `class`, not `media`: the user's explicit choice has to be able to beat the
  // operating system. A doctor on a dark-themed laptop may still want the light
  // theme on a bright ward, and vice versa at 3am.
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        canvas: "var(--rg-canvas)",
        surface: {
          DEFAULT: "var(--rg-surface)",
          raised: "var(--rg-surface-raised)",
          sunken: "var(--rg-surface-sunken)",
          hover: "var(--rg-surface-hover)",
        },
        ink: {
          DEFAULT: "var(--rg-text)",
          body: "var(--rg-text-body)",
          muted: "var(--rg-text-muted)",
          inverse: "var(--rg-text-inverse)",
        },
        line: {
          DEFAULT: "var(--rg-border)",
          strong: "var(--rg-border-strong)",
        },
        brand: {
          DEFAULT: "var(--rg-brand)",
          hover: "var(--rg-brand-hover)",
          subtle: "var(--rg-brand-subtle)",
          text: "var(--rg-brand-text)",
        },
        critical: {
          DEFAULT: "var(--rg-critical)",
          subtle: "var(--rg-critical-subtle)",
          line: "var(--rg-critical-line)",
          text: "var(--rg-critical-text)",
        },
        followup: {
          DEFAULT: "var(--rg-followup)",
          subtle: "var(--rg-followup-subtle)",
          line: "var(--rg-followup-line)",
          text: "var(--rg-followup-text)",
        },
        normal: {
          DEFAULT: "var(--rg-normal)",
          subtle: "var(--rg-normal-subtle)",
          line: "var(--rg-normal-line)",
          text: "var(--rg-normal-text)",
        },
        info: {
          subtle: "var(--rg-info-subtle)",
          line: "var(--rg-info-line)",
          text: "var(--rg-info-text)",
        },
      },
      borderRadius: {
        DEFAULT: "var(--rg-radius)",
        lg: "var(--rg-radius-lg)",
      },
      boxShadow: {
        sm: "var(--rg-shadow-sm)",
        DEFAULT: "var(--rg-shadow)",
        lg: "var(--rg-shadow-lg)",
      },
      fontFamily: {
        // System stack: zero network cost, and it is what the operating system
        // already renders best. A hospital PC on a slow VLAN should not wait on
        // a webfont to show a critical result.
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      fontSize: {
        // Slightly tighter tracking on headings, looser leading on body: this
        // is a reading interface, not a marketing page.
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.01em" }],
        xs: ["0.75rem", { lineHeight: "1.125rem" }],
        sm: ["0.8125rem", { lineHeight: "1.375rem" }],
        base: ["0.875rem", { lineHeight: "1.5rem" }],
        lg: ["1rem", { lineHeight: "1.625rem" }],
        xl: ["1.125rem", { lineHeight: "1.75rem", letterSpacing: "-0.005em" }],
        "2xl": ["1.375rem", { lineHeight: "1.875rem", letterSpacing: "-0.01em" }],
        "3xl": ["1.75rem", { lineHeight: "2.25rem", letterSpacing: "-0.015em" }],
      },
    },
  },
  plugins: [],
};
