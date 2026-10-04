import { MapPin, Thermometer } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { Video } from "@/api/client";
import { formatNumber } from "@/lib/format";

import { isDayPhase } from "./format";
import { PHASE_FILL } from "./phaseColors";

export interface Chip {
  key: string;
  icon: ReactNode;
  text: string;
}

/** Place, light and weather at the time of shooting, as small chips (empty when unknown). */
export function useContextChips(video: Video): Chip[] {
  const { t, i18n } = useTranslation();
  const phase = isDayPhase(video.light_phase) ? video.light_phase : null;
  const weather = video.weather_category
    ? t(`context.weatherCategory.${video.weather_category}`, {
        defaultValue: video.weather_category,
      })
    : null;
  const temperature =
    video.temperature_c !== null ? formatNumber(video.temperature_c, i18n.language, 0, "°C") : null;
  const chips: Chip[] = [];
  if (video.place) {
    chips.push({
      key: "place",
      icon: <MapPin className="size-3.5 shrink-0" aria-hidden />,
      text: video.place_approximate ? t("context.near", { place: video.place }) : video.place,
    });
  }
  if (phase) {
    chips.push({
      key: "phase",
      icon: (
        <span
          aria-hidden
          className="border-border inline-block size-2.5 shrink-0 rounded-full border"
          style={{ background: PHASE_FILL[phase] }}
        />
      ),
      text: t(`context.phase.${phase}`),
    });
  }
  if (weather || temperature) {
    chips.push({
      key: "weather",
      icon: <Thermometer className="size-3.5 shrink-0" aria-hidden />,
      text: [temperature, weather].filter(Boolean).join(" · "),
    });
  }
  return chips;
}
