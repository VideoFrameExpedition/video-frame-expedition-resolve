import { Languages, Monitor, Moon, Sun } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { LANGUAGES, setLanguage } from "@/i18n";
import { setThemePreference, type ThemePreference } from "@/lib/theme";
import { cn } from "@/lib/utils";

/** Theme and interface language, in the header. The analyses are shown, and exported, in the
 * interface's language: translated when they were written in the other one. */
export function PreferencesMenu() {
  const { t, i18n } = useTranslation();
  const language = i18n.language;
  const themes: { value: ThemePreference; icon: ReactNode }[] = [
    { value: "dark", icon: <Moon className="size-4" /> },
    { value: "light", icon: <Sun className="size-4" /> },
    { value: "system", icon: <Monitor className="size-4" /> },
  ];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t("theme.label")}>
          <Languages className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>{t("theme.label")}</DropdownMenuLabel>
        {themes.map((theme) => (
          <DropdownMenuItem
            key={theme.value}
            onSelect={() => {
              setThemePreference(theme.value);
            }}
          >
            {theme.icon}
            {t(`theme.${theme.value}`)}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuLabel>{t("language.label")}</DropdownMenuLabel>
        {LANGUAGES.map((item) => (
          <DropdownMenuItem
            key={item}
            onSelect={() => {
              setLanguage(item);
            }}
            className={cn(language === item && "font-semibold")}
          >
            {item === "fr" ? "Français" : "English"}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
