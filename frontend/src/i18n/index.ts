import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./en.json";
import fr from "./fr.json";

export const LANGUAGES = ["fr", "en"] as const;
export type Language = (typeof LANGUAGES)[number];

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
});

export default i18n;
