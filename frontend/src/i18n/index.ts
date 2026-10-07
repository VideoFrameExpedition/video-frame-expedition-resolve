import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./en.json";
import fr from "./fr.json";

export const LANGUAGES = ["fr", "en"] as const;
export type Language = (typeof LANGUAGES)[number];

/** The system of the application's computer, as /system/health names it. */
export type Platform = "windows" | "macos" | "linux";

interface Tree {
  [key: string]: string | Tree;
}
const RESOURCES: Record<Language, Tree> = { fr, en };

const STORAGE_KEY = "vfe.language";

/** The language chosen in this browser, if any (none: the default language, French). */
export function storedLanguage(): Language | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    if (value === "fr" || value === "en") {
      return value;
    }
  } catch {
    // Storage unavailable: default language.
  }
  return null;
}

export function setLanguage(language: Language): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Not persisted, still applied for this session.
  }
  void i18n.changeLanguage(language);
  document.documentElement.lang = language;
}

const initial: Language = typeof window === "undefined" ? "fr" : (storedLanguage() ?? "fr");
if (typeof document !== "undefined") {
  document.documentElement.lang = initial;
}

void i18n.use(initReactI18next).init({
  resources: { fr: { translation: fr }, en: { translation: en } },
  lng: initial,
  fallbackLng: "fr",
  interpolation: { escapeValue: false },
  returnNull: false,
  react: { bindI18nStore: "added" }, // texts of another system: the screens follow at once
});

/**
 * Texts that depend on the system of the application's computer: paths, shortcuts, the
 * launcher's name. The base texts are written for Windows; the « platform.<system> » tree of
 * each language replaces them on that system (macOS has one, Linux keeps the base texts).
 */
export function applyPlatform(platform: Platform): void {
  for (const language of LANGUAGES) {
    const variants = (RESOURCES[language].platform as Tree | undefined)?.[platform];
    if (typeof variants === "object") {
      i18n.addResourceBundle(language, "translation", structuredClone(variants), true, true);
    }
  }
}

export default i18n;
