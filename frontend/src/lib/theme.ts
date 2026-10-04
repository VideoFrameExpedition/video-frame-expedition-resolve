import { useSyncExternalStore } from "react";

export type ThemePreference = "dark" | "light" | "system";
export type ResolvedTheme = "dark" | "light";

const STORAGE_KEY = "vfe.theme";
const listeners = new Set<() => void>();

function readPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "dark" || stored === "light" || stored === "system") {
      return stored;
    }
  } catch {
    // Storage can be unavailable (private mode, blocked site data): fall back to the default.
  }
  return "dark";
}

let preference: ThemePreference = typeof window === "undefined" ? "dark" : readPreference();

function systemTheme(): ResolvedTheme {
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function resolveTheme(pref: ThemePreference = preference): ResolvedTheme {
  return pref === "system" ? systemTheme() : pref;
}

export function applyTheme(): void {
  document.documentElement.classList.toggle("dark", resolveTheme() === "dark");
}

export function setThemePreference(next: ThemePreference): void {
  preference = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // Non-persistent preference is acceptable.
  }
  applyTheme();
  listeners.forEach((listener) => {
    listener();
  });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const media = window.matchMedia("(prefers-color-scheme: light)");
  const onChange = (): void => {
    if (preference === "system") {
      applyTheme();
      listener();
    }
  };
  media.addEventListener("change", onChange);
  return () => {
    listeners.delete(listener);
    media.removeEventListener("change", onChange);
  };
}

export function useTheme(): { preference: ThemePreference; resolved: ResolvedTheme } {
  const current = useSyncExternalStore(subscribe, () => preference);
  return { preference: current, resolved: resolveTheme(current) };
}
