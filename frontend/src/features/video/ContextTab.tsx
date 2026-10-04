import { CloudSun, Copy, ExternalLink, Info, MapPin, MapPinOff, MoonStar, Sun } from "lucide-react";
import { lazy, Suspense, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { SunContext, VideoContext, VideoDetail, WeatherContext } from "@/api/client";
import { useTrack, useVideoContext } from "@/api/queries";
import { StatTile } from "@/components/charts/StatTile";
import { Fact } from "@/components/common";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber, formatPercent } from "@/lib/format";

import { locationKey } from "./captureTime";
import { DayStrip } from "./context/DayStrip";
import { SunElevationChart } from "./context/SunElevationChart";
import {
  compassKey,
  isDayPhase,
  localClock,
  localMinutes,
  type LocalZone,
  phaseSegments,
  toDms,
} from "./context/format";

const ContextMap = lazy(() => import("./context/ContextMap"));

function ItemIcon({ icon: Icon }: { icon: typeof Sun }) {
  return <Icon className="text-muted-foreground size-4" aria-hidden />;
}

function LocationCard({
  context,
  video,
  fresh,
}: {
  context: VideoContext;
  video: VideoDetail;
  fresh: boolean;
}) {
  const { t, i18n } = useTranslation();
  const track = useTrack(video.id);
  const [copied, setCopied] = useState(false);
  const { latitude, longitude } = context;
  const points = useMemo(
    () => (track.data ?? []).map((p) => [p.latitude, p.longitude] as const),
    [track.data],
  );
  if (latitude === null || longitude === null) return null;
  const place = context.place;
  const folderDefault = context.location_source === "folder_default";
  const label = place?.label ?? `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`;
  const coordinates = `${latitude.toFixed(5)}, ${longitude.toFixed(5)}`;
  const osm = `https://www.openstreetmap.org/?mlat=${latitude.toFixed(5)}&mlon=${longitude.toFixed(5)}#map=16/${latitude.toFixed(5)}/${longitude.toFixed(5)}`;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ItemIcon icon={MapPin} />
          {t("context.location")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {/* Mounted only on a flag fetched now: a cached "online" must not request tiles. */}
        {context.online_services && fresh ? (
          <Suspense fallback={<Skeleton className="h-72 w-full" />}>
            <ContextMap latitude={latitude} longitude={longitude} track={points} label={label} />
          </Suspense>
        ) : (
          <p className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
            {t("context.mapHidden")}
          </p>
        )}
        <div className="grid gap-1">
          {place ? (
            <>
              <p className="text-base font-medium">{place.label ?? t("context.unnamed")}</p>
              {folderDefault ? (
                <p className="text-warning text-sm">{t("context.folderDefault")}</p>
              ) : null}
              {!folderDefault && (place.sublocality || place.road || place.postcode) ? (
                <p className="text-muted-foreground text-sm">
                  {[place.road, place.sublocality, place.postcode].filter(Boolean).join(" · ")}
                </p>
              ) : null}
              {place.feature && !folderDefault ? (
                <p className="text-sm">
                  {t("context.nearby", {
                    name: place.feature.name,
                    type: t(`context.featureType.${place.feature.type}`, {
                      defaultValue: place.feature.type,
                    }),
                    distance: formatNumber(place.feature.distance_m, i18n.language, 0, "m"),
                  })}
                </p>
              ) : null}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Badge variant={place.approximate || folderDefault ? "outline" : "secondary"}>
                  {t(
                    `context.placeSource.${place.source === "nominatim" ? "nominatim" : "offline"}`,
                  )}
                </Badge>
                <span className="text-muted-foreground text-xs">{place.attribution}</span>
              </div>
            </>
          ) : null}
        </div>
        <dl className="divide-y">
          <Fact
            label={t("context.coordinates")}
            value={
              <span className="tabular-nums">
                {coordinates}
                <span className="text-muted-foreground block text-xs">
                  {toDms(latitude, "lat")} · {toDms(longitude, "lon")}
                </span>
              </span>
            }
          />
          {context.altitude_m !== null ? (
            <Fact
              label={t("context.altitude")}
              value={formatNumber(context.altitude_m, i18n.language, 0, "m")}
            />
          ) : null}
          <Fact
            label={t("context.positionSource")}
            value={t(`capture.locationSource.${locationKey(context.location_source)}`)}
          />
          {place?.grid_precision_m ? (
            <Fact
              label={t("context.sent")}
              value={t("context.sentValue", { meters: place.grid_precision_m })}
            />
          ) : null}
        </dl>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void navigator.clipboard.writeText(coordinates).then(() => {
                setCopied(true);
              });
            }}
          >
            <Copy aria-hidden />
            {copied ? t("context.copied") : t("context.copy")}
          </Button>
          {context.online_services && fresh ? (
            <Button variant="outline" size="sm" asChild>
              <a href={osm} target="_blank" rel="noreferrer">
                <ExternalLink aria-hidden />
                {t("context.openOsm")}
              </a>
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function kelvinRange(range: number[] | null | undefined, locale: string): string {
  if (range?.length !== 2) return "—";
  return `${formatNumber(range[0], locale)}–${formatNumber(range[1], locale, 0, "K")}`;
}

function SunCard({
  sun,
  context,
  zone,
}: {
  sun: SunContext;
  context: VideoContext;
  zone: LocalZone;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const events = sun.events;
  const fallback = isDayPhase(sun.phase_at_instant) ? sun.phase_at_instant : "day";
  const segments = phaseSegments(events, zone, fallback);
  const capture = localMinutes(sun.at_utc, zone);
  const probable = sun.time_confidence === "medium";
  const takeMinutes = sun.take
    ? Math.round((Date.parse(sun.take.end) - Date.parse(sun.at_utc)) / 60_000)
    : null;
  const probableNote = probable ? t("context.probableValue") : undefined;
  const phase = sun.light_phase ? t(`context.phase.${sun.light_phase}`) : t("context.uncertain");
  const part = sun.day_part ? t(`context.dayPart.${sun.day_part}`) : null;
  const light = sun.light;
  const elevation = sun.apparent_elevation_deg;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ItemIcon icon={Sun} />
          {t("context.sun")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile
            label={t("context.phaseLabel")}
            value={phase}
            note={part ?? (sun.direction ? t(`context.direction.${sun.direction}`) : undefined)}
            warning={sun.light_phase ? undefined : t("context.uncertainNote")}
          />
          <StatTile
            label={t("context.elevation")}
            value={`${formatNumber(elevation, locale, 1)}°`}
            note={
              sun.window && probable
                ? t("context.elevationRange", {
                    min: formatNumber(sun.window.elevation_min, locale, 1),
                    max: formatNumber(sun.window.elevation_max, locale, 1),
                  })
                : elevation >= 0
                  ? t("context.aboveHorizon")
                  : t("context.belowHorizon")
            }
            warning={probableNote}
          />
          <StatTile
            label={t("context.azimuth")}
            value={`${formatNumber(sun.azimuth_deg, locale)}°`}
            note={t(`context.compass.${compassKey(sun.azimuth_deg)}`)}
            warning={probableNote}
          />
          <StatTile
            label={t("context.theoreticalK")}
            value={light ? kelvinRange(light.direct_k ?? light.ambient_k, locale) : "—"}
            note={light ? t(`context.regime.${light.regime}`) : t("context.noDaylight")}
            warning={light?.off_locus ? t("context.indicative") : probableNote}
          />
        </div>
        {context.sun_curve ? (
          <SunElevationChart
            curve={context.sun_curve}
            capture={capture}
            captureElevation={elevation}
          />
        ) : null}
        <DayStrip
          segments={segments}
          capture={capture}
          windowMinutes={sun.window ? 30 : null}
          takeMinutes={takeMinutes}
        />
        <dl className="grid gap-x-8 sm:grid-cols-2">
          <Fact
            label={t("context.captureTime")}
            value={`${localClock(sun.at_utc, zone, locale)}${probable ? ` (${t("context.probable")})` : ""}`}
          />
          <Fact label={t("context.sunrise")} value={localClock(events.rising.sun, zone, locale)} />
          <Fact label={t("context.sunset")} value={localClock(events.setting.sun, zone, locale)} />
          <Fact
            label={t("context.goldenMorning")}
            value={`${localClock(events.rising.blue_golden, zone, locale)}–${localClock(events.rising.golden_day, zone, locale)}`}
          />
          <Fact
            label={t("context.goldenEvening")}
            value={`${localClock(events.setting.golden_day, zone, locale)}–${localClock(events.setting.blue_golden, zone, locale)}`}
          />
          <Fact
            label={t("context.solarNoon")}
            value={localClock(events.solar_noon, zone, locale)}
          />
          {events.polar ? (
            <Fact label={t("context.polarLabel")} value={t(`context.polar.${events.polar}`)} />
          ) : null}
        </dl>
        {sun.window && !sun.light_phase ? (
          <Alert>
            <Info aria-hidden />
            <AlertDescription>
              {t("context.windowNote", {
                phases: sun.window.phases.map((p) => t(`context.phase.${p}`)).join(" → "),
              })}
            </AlertDescription>
          </Alert>
        ) : null}
        {sun.take && sun.take.phases.length > 1 ? (
          <p className="text-muted-foreground text-sm">
            {t("context.takeNote", {
              phases: sun.take.phases.map((p) => t(`context.phase.${p}`)).join(" → "),
            })}
          </p>
        ) : null}
        <div className="grid gap-2 rounded-lg border p-3">
          <p className="text-sm font-medium">{t("context.lightTitle")}</p>
          {light ? (
            <dl className="divide-y">
              {light.direct_k ? (
                <Fact label={t("context.direct")} value={kelvinRange(light.direct_k, locale)} />
              ) : null}
              <Fact label={t("context.ambient")} value={kelvinRange(light.ambient_k, locale)} />
              {context.measured_cct_k ? (
                <Fact
                  label={t("context.measured")}
                  value={formatNumber(context.measured_cct_k, locale, 0, "K")}
                />
              ) : null}
            </dl>
          ) : (
            <p className="text-muted-foreground text-sm">{t("context.noDaylight")}</p>
          )}
          {context.light_comparison ? (
            <p className="text-sm">{t(`context.comparison.${context.light_comparison}`)}</p>
          ) : null}
          {light?.off_locus ? (
            <p className="text-muted-foreground text-xs">{t("context.offLocus")}</p>
          ) : null}
          <p className="text-muted-foreground text-xs">
            {t("context.assumptionsLabel")}{" "}
            {sun.assumptions.map((a) => t(`context.assumption.${a}`)).join(" · ")}
          </p>
        </div>
        {sun.elevation_deg < -0.833 ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <MoonStar className="size-4" aria-hidden />
            {t("context.moon", {
              percent: formatPercent(sun.moon.illuminated),
              trend: t(sun.moon.waxing ? "context.waxing" : "context.waning"),
            })}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function WeatherCard({ weather, zone }: { weather: WeatherContext; zone: LocalZone }) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const v = weather.values;
  const code = weather.weather_code;
  const label =
    code === null
      ? t("context.weatherUnknown")
      : t(`context.weatherCode.${code}`, { defaultValue: t("context.weatherCodeN", { code }) });
  const wind =
    v.wind_speed_kmh === null
      ? "—"
      : `${formatNumber(v.wind_speed_kmh, locale, 0, "km/h")}${
          v.wind_direction_deg === null
            ? ""
            : ` · ${t(`context.compass.${compassKey(v.wind_direction_deg)}`)}`
        }`;
  const layers = [v.cloud_low_pct, v.cloud_mid_pct, v.cloud_high_pct];
  const distance = weather.grid?.distance_km;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ItemIcon icon={CloudSun} />
          {t("context.weather")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <Alert>
          <Info aria-hidden />
          <AlertDescription>
            {t("context.weatherCaveat", {
              distance: distance === undefined ? "?" : formatNumber(distance, locale, 1, "km"),
            })}
            {weather.provisional ? ` ${t("context.provisional")}` : ""}
          </AlertDescription>
        </Alert>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile
            label={t("context.conditions")}
            value={label}
            note={
              weather.sun_fraction === null
                ? undefined
                : t("context.sunshine", { percent: formatPercent(weather.sun_fraction) })
            }
          />
          <StatTile
            label={t("context.temperature")}
            value={formatNumber(v.temperature_c, locale, 1, "°C")}
            note={t("context.feelsLike", {
              value: formatNumber(v.apparent_temperature_c, locale, 1, "°C"),
            })}
          />
          <StatTile
            label={t("context.wind")}
            value={wind}
            note={
              v.wind_gusts_kmh === null
                ? undefined
                : t("context.gusts", { value: formatNumber(v.wind_gusts_kmh, locale, 0, "km/h") })
            }
          />
          <StatTile
            label={t("context.clouds")}
            value={v.cloud_cover_pct === null ? "—" : formatPercent(v.cloud_cover_pct / 100)}
            note={
              layers.every((x) => x !== null)
                ? t("context.cloudLayers", {
                    values: layers.map((x) => formatNumber(x, locale)).join(" / "),
                  })
                : undefined
            }
          />
        </div>
        <dl className="grid gap-x-8 sm:grid-cols-2">
          <Fact
            label={t("context.humidity")}
            value={`${formatNumber(v.relative_humidity_pct, locale, 0, "%")} · ${t("context.dewPoint", { value: formatNumber(v.dew_point_c, locale, 1, "°C") })}`}
          />
          <Fact
            label={t("context.precipitation")}
            value={`${formatNumber(v.precipitation_mm, locale, 1, "mm")} · ${t("context.last3h", { value: formatNumber(v.precipitation_last_3h_mm, locale, 1, "mm") })}`}
          />
          {v.snowfall_cm ? (
            <Fact
              label={t("context.snowfall")}
              value={formatNumber(v.snowfall_cm, locale, 1, "cm")}
            />
          ) : null}
          <Fact
            label={t("context.visibility")}
            value={
              v.visibility_m === null ? "—" : formatNumber(v.visibility_m / 1000, locale, 1, "km")
            }
          />
          <Fact
            label={t("context.pressure")}
            value={formatNumber(v.pressure_hpa, locale, 0, "hPa")}
          />
          <Fact
            label={t("context.radiation")}
            value={`${formatNumber(v.shortwave_wm2, locale, 0, "W/m²")}${
              weather.diffuse_fraction === null
                ? ""
                : ` · ${t("context.diffuse", { percent: formatPercent(weather.diffuse_fraction) })}`
            }`}
          />
          <Fact
            label={t("context.weatherSource")}
            value={t(`context.weatherSources.${weather.source}`)}
          />
        </dl>
        {weather.time_confidence === "medium" && weather.hours.length ? (
          <div className="grid gap-2">
            <p className="text-sm font-medium">{t("context.hoursTitle")}</p>
            <table className="w-full text-sm tabular-nums">
              <thead className="text-muted-foreground text-left text-xs">
                <tr>
                  <th className="py-1 font-normal">{t("context.hour")}</th>
                  <th className="py-1 font-normal">{t("context.temperature")}</th>
                  <th className="py-1 font-normal">{t("context.clouds")}</th>
                  <th className="py-1 font-normal">{t("context.precipitation")}</th>
                </tr>
              </thead>
              <tbody>
                {weather.hours.map((hour) => (
                  <tr key={hour.t} className="border-t">
                    <td className="py-1">{localClock(hour.t, zone, locale)}</td>
                    <td className="py-1">{formatNumber(hour.temperature_2m, locale, 1, "°C")}</td>
                    <td className="py-1">
                      {hour.cloud_cover === null ? "—" : formatPercent(hour.cloud_cover / 100)}
                    </td>
                    <td className="py-1">{formatNumber(hour.precipitation, locale, 1, "mm")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <p className="text-muted-foreground text-xs">
          <a className="underline" href={weather.attribution_url} target="_blank" rel="noreferrer">
            {weather.attribution}
          </a>
          {weather.notice ? ` · ${weather.notice}` : ""}
        </p>
      </CardContent>
    </Card>
  );
}

export function ContextTab({ video }: { video: VideoDetail }) {
  const { t } = useTranslation();
  const context = useVideoContext(video.id);
  if (context.isPending) return <Skeleton className="h-72 w-full" />;
  if (context.isError) return null;
  const data = context.data;
  const zone: LocalZone = {
    timeZone: video.capture_timezone,
    offsetMin: video.capture_utc_offset_min,
  };
  const notes = Object.entries(data.notes);
  const nothing = !data.place && !data.sun && !data.weather && data.latitude === null;

  return (
    <div className="grid gap-6">
      {!data.online_services ? (
        <Alert>
          <Info aria-hidden />
          <AlertDescription>
            {t(data.weather ? "context.offlineNoticeKept" : "context.offlineNotice")}
          </AlertDescription>
        </Alert>
      ) : null}
      {data.latitude === null ? (
        <Alert>
          <MapPinOff aria-hidden />
          <AlertDescription className="grid gap-1">
            <p className="text-foreground font-medium">{t("context.noGpsTitle")}</p>
            <p>{t("context.noGpsBody")}</p>
          </AlertDescription>
        </Alert>
      ) : null}
      <LocationCard context={data} video={video} fresh={context.isFetchedAfterMount} />
      {data.sun ? <SunCard sun={data.sun} context={data} zone={zone} /> : null}
      {data.weather ? <WeatherCard weather={data.weather} zone={zone} /> : null}
      {notes.length && !nothing ? (
        <ul className="text-muted-foreground grid gap-1 text-sm">
          {notes.map(([stage, reason]) => (
            <li key={stage}>
              {t("context.note", {
                part: t(`context.part.${stage}`, { defaultValue: stage }),
                reason,
              })}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
