/**
 * Light / dark, remembered.
 *
 * Not a preference so much as a working condition: the people most likely to
 * miss something are on a night shift, and a white screen at 3am in a dim ward
 * is genuinely hard to read. The OS setting is respected by default, and this
 * only exists so someone can *override* it — a doctor on a dark-themed laptop
 * may still want light on a bright ward round.
 *
 * The initial class is applied by an inline script in `index.html`, before
 * first paint, so there is no flash of the wrong theme. This component only
 * has to keep the toggle in sync afterwards.
 */

import { useEffect, useState } from "react";

const KEY = "rg-theme";

type Theme = "light" | "dark" | "system";

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  const dark = theme === "dark" || (theme === "system" && systemPrefersDark());
  root.classList.toggle("dark", dark);
  // `.light` is what lets an explicit light choice beat a dark OS: the
  // prefers-color-scheme block in tokens.css is scoped to :root:not(.light).
  root.classList.toggle("light", theme === "light");
}

function readStored(): Theme {
  try {
    const raw = localStorage.getItem(KEY);
    return raw === "light" || raw === "dark" ? raw : "system";
  } catch {
    // Private browsing, or storage disabled by policy. Not worth failing over.
    return "system";
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readStored);

  useEffect(() => {
    applyTheme(theme);
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch {
      // Ignore: the theme still applies for this session.
    }
  }, [theme]);

  // Follow the OS while the user has not chosen — someone whose laptop dims at
  // sunset should not have to touch this.
  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const onChange = () => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  const isDark =
    theme === "dark" || (theme === "system" && systemPrefersDark());

  return (
    <button
      type="button"
      // The accessible name says what pressing it *does*, not what the current
      // state is — "Dark mode" leaves a screen-reader user guessing whether
      // that is a description or an instruction.
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      title={isDark ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="inline-flex size-8 items-center justify-center rounded text-ink-muted
                 transition-colors hover:bg-surface-hover hover:text-ink"
    >
      {isDark ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function SunIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      className="size-4"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-4"
    >
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
    </svg>
  );
}
