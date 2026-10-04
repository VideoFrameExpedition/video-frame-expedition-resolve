import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { FacetValue, SearchFacets } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  folderOptions,
  hasFilters,
  SEARCH_KINDS,
  USABILITY_STEPS,
  type Option,
  type RootFolders,
  type SearchPageSearch,
} from "./searchState";

const ALL = "all";

function FilterSelect({
  label,
  value,
  options,
  onChange,
  wide = false,
}: {
  label: string; // « Any weather »: the control's name and its « any » choice
  value: string | undefined;
  options: Option[];
  onChange: (value: string | undefined) => void;
  wide?: boolean;
}) {
  return (
    <Select
      value={value ?? ALL}
      onValueChange={(next) => {
        onChange(next === ALL ? undefined : next);
      }}
    >
      <SelectTrigger
        className={wide ? "w-full sm:w-56" : "w-[calc(50%-0.25rem)] sm:w-44"}
        aria-label={label}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>{label}</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function counted(values: readonly FacetValue[], label: (value: string) => string): Option[] {
  return values.map((item) => ({
    value: item.value,
    label: `${label(item.value)} (${item.count})`,
  }));
}

/**
 * One row of filters, fed by what the index holds: a filter with nothing to choose from is not
 * shown. Every change goes to the address (``onChange``).
 */
export function SearchFilterBar({
  search,
  facets,
  folders,
  onChange,
}: {
  search: SearchPageSearch;
  facets: SearchFacets | undefined;
  folders: readonly RootFolders[] | undefined;
  onChange: (patch: Partial<SearchPageSearch>, clear?: boolean) => void;
}) {
  const { t } = useTranslation();
  if (!facets) {
    return null;
  }
  const kinds = SEARCH_KINDS.filter((kind) => facets.kinds.some((k) => k.value === kind));
  const folderChoices = folderOptions(folders ?? []);
  const folder = search.root !== undefined ? `${search.root}:${search.folder ?? ""}` : undefined;
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      role="group"
      aria-label={t("search.filters")}
    >
      {kinds.length > 1 ? (
        <FilterSelect
          label={t("search.any.kind")}
          value={search.kind}
          options={kinds.map((kind) => ({ value: kind, label: t(`search.kinds.${kind}`) }))}
          onChange={(kind) => {
            onChange({ kind: kind as SearchPageSearch["kind"] });
          }}
        />
      ) : null}
      {facets.weather.length > 0 ? (
        <FilterSelect
          label={t("search.any.weather")}
          value={search.weather}
          options={counted(facets.weather, (w) => t(`context.weatherCategory.${w}`))}
          onChange={(weather) => {
            onChange({ weather: weather as SearchPageSearch["weather"] });
          }}
        />
      ) : null}
      {facets.light_phases.length > 0 ? (
        <FilterSelect
          label={t("search.any.light")}
          value={search.light}
          options={counted(facets.light_phases, (p) => t(`context.phase.${p}`))}
          onChange={(light) => {
            onChange({ light: light as SearchPageSearch["light"] });
          }}
        />
      ) : null}
      {facets.places.length > 0 ? (
        <FilterSelect
          label={t("search.any.place")}
          value={search.place}
          options={counted(facets.places, (p) => p)}
          onChange={(place) => {
            onChange({ place });
          }}
        />
      ) : null}
      {facets.subjects.length > 0 ? (
        <FilterSelect
          label={t("search.any.subject")}
          value={search.subject}
          options={counted(facets.subjects, (s) => s)}
          onChange={(subject) => {
            onChange({ subject });
          }}
        />
      ) : null}
      {facets.shot_types.length > 0 ? (
        <FilterSelect
          label={t("search.any.shot")}
          value={search.shot}
          options={counted(facets.shot_types, (s) => t(`analysis.shot_type.${s}`))}
          onChange={(shot) => {
            onChange({ shot: shot as SearchPageSearch["shot"] });
          }}
        />
      ) : null}
      {facets.speech ? (
        <FilterSelect
          label={t("search.any.speech")}
          value={search.speech}
          options={[
            { value: "yes", label: t("search.speech.yes") },
            { value: "no", label: t("search.speech.no") },
          ]}
          onChange={(speech) => {
            onChange({ speech: speech as SearchPageSearch["speech"] });
          }}
        />
      ) : null}
      {facets.usability ? (
        <FilterSelect
          label={t("search.any.usability")}
          value={search.usability?.toString()}
          options={USABILITY_STEPS.map((step) => ({
            value: step.toString(),
            label: t("search.usability", { value: step }),
          }))}
          onChange={(value) => {
            onChange({ usability: value === undefined ? undefined : Number(value) });
          }}
        />
      ) : null}
      {facets.devices.length > 0 ? (
        <FilterSelect
          label={t("search.any.device")}
          value={search.device}
          options={counted(facets.devices, (d) => d)}
          onChange={(device) => {
            onChange({ device });
          }}
        />
      ) : null}
      {facets.orientations.length > 1 ? (
        <FilterSelect
          label={t("search.any.orientation")}
          value={search.orientation}
          options={counted(facets.orientations, (o) => t(`orientation.${o}`))}
          onChange={(orientation) => {
            onChange({ orientation: orientation as SearchPageSearch["orientation"] });
          }}
        />
      ) : null}
      {facets.rated ? (
        <FilterSelect
          label={t("search.any.rating")}
          value={search.rating?.toString()}
          options={[1, 2, 3, 4, 5].map((value) => ({
            value: value.toString(),
            label: t("search.rating", { value }),
          }))}
          onChange={(value) => {
            onChange({ rating: value === undefined ? undefined : Number(value) });
          }}
        />
      ) : null}
      {facets.favorites ? (
        <FilterSelect
          label={t("search.any.favorite")}
          value={search.favorite ? "yes" : undefined}
          options={[{ value: "yes", label: t("search.favorite") }]}
          onChange={(value) => {
            onChange({ favorite: value ? true : undefined });
          }}
        />
      ) : null}
      {folderChoices.length > 1 ? (
        <FilterSelect
          wide
          label={t("search.any.folder")}
          value={folder}
          options={folderChoices}
          onChange={(value) => {
            if (value === undefined) {
              onChange({ root: undefined, folder: undefined });
              return;
            }
            const at = value.indexOf(":");
            onChange({ root: value.slice(0, at), folder: value.slice(at + 1) });
          }}
        />
      ) : null}
      {facets.date_min ? (
        <div className="flex w-full items-center gap-1 sm:w-auto">
          <Input
            type="date"
            className="min-w-0 flex-1 sm:w-38 sm:flex-none"
            aria-label={t("search.dateFrom")}
            min={facets.date_min}
            max={facets.date_max ?? undefined}
            value={search.from ?? ""}
            onChange={(e) => {
              onChange({ from: e.target.value || undefined });
            }}
          />
          <span className="text-muted-foreground text-sm" aria-hidden>
            –
          </span>
          <Input
            type="date"
            className="min-w-0 flex-1 sm:w-38 sm:flex-none"
            aria-label={t("search.dateTo")}
            min={facets.date_min}
            max={facets.date_max ?? undefined}
            value={search.to ?? ""}
            onChange={(e) => {
              onChange({ to: e.target.value || undefined });
            }}
          />
        </div>
      ) : null}
      {hasFilters(search) ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            onChange({}, true);
          }}
        >
          <X className="size-4" aria-hidden />
          {t("search.clear")}
        </Button>
      ) : null}
    </div>
  );
}
