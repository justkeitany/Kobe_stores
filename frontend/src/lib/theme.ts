import { useEffect, useState } from "react";

/**
 * Theme toggle. Dark is the default ("Obsidian Flux"); selecting light adds
 * `light` to <html>, which re-points the CSS variables in index.css.
 * Preference is persisted to localStorage.
 */
export type Theme = "dark" | "light";

const KEY = "iptv-theme";

function apply(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle("light", theme === "light");
  root.style.colorScheme = theme;
}

export function getInitialTheme(): Theme {
  const saved = localStorage.getItem(KEY);
  return saved === "light" ? "light" : "dark";
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    apply(theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);

  return {
    theme,
    setTheme,
    toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  };
}
