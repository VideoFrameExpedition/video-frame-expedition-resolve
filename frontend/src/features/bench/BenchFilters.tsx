import { X } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { BenchModel } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import { FamilyDot } from "./CodeBadges";
import {
  familyOf,
  filtering,
  NO_FILTERS,
  PARAMS_CHIPS,
  paramsStep,
  QUANT_CHIPS,
  quantStep,
  type Chip,
  type ModelFilters,
} from "./modelCodes";

function Toggle({
  pressed,
  dimmed,
  chip,
  label,
  count,
  onToggle,
  children,
}: {
  pressed: boolean;
  dimmed: boolean; // another one of its kind is chosen
  chip?: Chip;
  label: string;
  count: number;
  onToggle: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={t("bench.filters.option", { label, count })}
      onClick={onToggle}
      className={cn(
        "focus-visible:ring-ring inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap ring-1 ring-black/10 transition ring-inset focus-visible:ring-2 focus-visible:outline-none dark:ring-white/15",
        !chip && "bg-secondary text-secondary-foreground hover:bg-secondary/70",
        pressed && "ring-foreground ring-2",
        dimmed && !pressed && "opacity-45 hover:opacity-80",
        count === 0 && !pressed && "opacity-35",
      )}
      style={chip ? { backgroundColor: chip.background, color: chip.ink } : undefined}
    >
      {children}
      <span className="font-mono text-[0.7rem] opacity-75">{count}</span>
    </button>
  );
}

function Line({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-muted-foreground w-28 shrink-0 text-xs">{title}</span>
      {children}
    </div>
  );
}

/** The filters of the page, at the top: the families of LM Studio and the steps of the
 * parameters and of the quantization, in the colours of the lists, so that they read as their
 * legend too. They keep the models to tick and the results shown below. */
export function BenchFilters({
  models,
  filters,
  onChange,
}: {
  models: readonly BenchModel[]; // those of LM Studio: what the counts are about
  filters: ModelFilters;
  onChange: (filters: ModelFilters) => void;
}) {
  const { t } = useTranslation();
  const families = [...new Set(models.map(familyOf))].sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }),
  );
  const flip = <T,>(list: readonly T[], value: T): T[] =>
    list.includes(value) ? list.filter((other) => other !== value) : [...list, value];
  const steps = (
    kind: "params" | "quants",
    chips: readonly Chip[],
    stepOfModel: (model: BenchModel) => number | null,
  ) =>
    chips.map((chip, step) => {
      const label = t(`bench.codes.${kind === "params" ? "params" : "quant"}Steps.${step}`);
      return (
        <Toggle
          key={chip.background}
          pressed={filters[kind].includes(step)}
          dimmed={filters[kind].length > 0}
          chip={chip}
          label={label}
          count={models.filter((model) => stepOfModel(model) === step).length}
          onToggle={() => {
            onChange({ ...filters, [kind]: flip(filters[kind], step) });
          }}
        >
          {label}
        </Toggle>
      );
    });

  return (
    <Card>
      <CardContent className="grid gap-2.5 py-4">
        <div className="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="text-sm font-medium">{t("bench.filters.title")}</h2>
          <span className="text-muted-foreground text-xs">{t("bench.filters.hint")}</span>
          {filtering(filters) ? (
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto"
              onClick={() => {
                onChange(NO_FILTERS);
              }}
            >
              <X className="size-3.5" aria-hidden />
              {t("bench.filters.clear")}
            </Button>
          ) : null}
        </div>
        <div className="grid gap-2" role="group" aria-label={t("bench.filters.title")}>
          <Line title={t("bench.filters.family")}>
            {families.map((family) => {
              const label = family || t("bench.tree.noFamily");
              return (
                <Toggle
                  key={family}
                  pressed={filters.families.includes(family)}
                  dimmed={filters.families.length > 0}
                  label={label}
                  count={models.filter((model) => familyOf(model) === family).length}
                  onToggle={() => {
                    onChange({ ...filters, families: flip(filters.families, family) });
                  }}
                >
                  <FamilyDot family={family} />
                  {label}
                </Toggle>
              );
            })}
          </Line>
          <Line title={t("bench.codes.params")}>
            {steps("params", PARAMS_CHIPS, (model) => paramsStep(model.params))}
          </Line>
          <Line title={t("bench.codes.quant")}>
            {steps("quants", QUANT_CHIPS, (model) => quantStep(model.quantization))}
          </Line>
        </div>
      </CardContent>
    </Card>
  );
}
