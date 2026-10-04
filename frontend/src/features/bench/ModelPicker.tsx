import { FlaskConical } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type BenchModel, type BenchOverview } from "@/api/client";
import { useStartBench } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { formatNumber } from "@/lib/format";

import { fileGigabytes, gigabytes } from "./benchFormat";

const STORAGE_KEY = "vfe.bench.models";

/** The models ticked last time in this browser (those LM Studio still holds are ticked again). */
function storedSelection(): string[] {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((key) => typeof key === "string") : [];
  } catch {
    return [];
  }
}

function storeSelection(keys: string[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
  } catch {
    // Not remembered, still used for this test.
  }
}

function ModelRow({
  model,
  checked,
  onChange,
}: {
  model: BenchModel;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const tooBig = model.fit === "too_big";
  return (
    <li className="border-b last:border-b-0">
      <label
        className={
          tooBig
            ? "flex cursor-not-allowed items-start gap-3 px-1 py-2.5 opacity-60"
            : "hover:bg-secondary/60 flex cursor-pointer items-start gap-3 rounded-lg px-1 py-2.5"
        }
      >
        <input
          type="checkbox"
          className="accent-primary mt-1 size-4 shrink-0"
          checked={checked}
          disabled={tooBig}
          onChange={(event) => {
            onChange(event.target.checked);
          }}
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium">{model.display_name}</span>
            {model.params ? <Badge variant="secondary">{model.params}</Badge> : null}
            {model.quantization ? <Badge variant="secondary">{model.quantization}</Badge> : null}
            {model.loaded ? (
              <Badge className="bg-accent text-accent-foreground border-0">
                {t("bench.new.loadedBadge")}
              </Badge>
            ) : null}
          </span>
          <span className="text-muted-foreground block truncate font-mono text-xs">
            {model.key}
          </span>
          {model.fit === "tight" ? (
            <span className="text-warning-ink block text-xs">{t("bench.new.tight")}</span>
          ) : null}
        </span>
        <span className="text-muted-foreground shrink-0 text-sm tabular-nums">
          {formatNumber(fileGigabytes(model.size_bytes), i18n.language, 1, t("bench.unit.gb"))}
        </span>
      </label>
    </li>
  );
}

/** « New test »: the vision models of LM Studio to tick, and the launch. */
export function ModelPicker({
  overview,
  onStarted,
}: {
  overview: BenchOverview;
  onStarted: (runId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const start = useStartBench();
  const imagesId = useId();
  const available = new Set(
    overview.models.filter((model) => model.fit !== "too_big").map((model) => model.key),
  );
  const [selected, setSelected] = useState<string[]>(storedSelection);
  const [images, setImages] = useState<number>(overview.default_images);
  const ticked = selected.filter((key) => available.has(key));
  const noFrames = overview.frames_available < overview.min_images;
  const active = Boolean(overview.active_run_id);
  // A small library is shown whole: its frame count replaces the choices it cannot fill.
  const shown = Math.min(images, Math.max(overview.frames_available, overview.min_images));
  const choices = [
    ...new Set([
      ...overview.image_choices.filter((count) => count <= overview.frames_available),
      shown,
    ]),
  ].sort((a, b) => a - b);
  const gigabyte = t("bench.unit.gb");

  const toggle = (key: string, checked: boolean): void => {
    const next = checked ? [...ticked, key] : ticked.filter((other) => other !== key);
    setSelected(next);
    storeSelection(next);
  };
  const row = (model: BenchModel) => (
    <ModelRow
      key={model.key}
      model={model}
      checked={ticked.includes(model.key)}
      onChange={(checked) => {
        toggle(model.key, checked);
      }}
    />
  );
  const testable = overview.models.filter((model) => model.fit !== "too_big");
  const tooBig = overview.models.filter((model) => model.fit === "too_big");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FlaskConical className="text-brand-teal size-4" aria-hidden />
          {t("bench.new.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-muted-foreground max-w-prose text-sm">{t("bench.new.intro")}</p>
        {overview.lmstudio_error ? (
          <p className="text-destructive text-sm">{t("bench.new.unreachable")}</p>
        ) : overview.models.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("bench.new.noModels")}</p>
        ) : (
          <>
            <ul className="grid gap-x-8 lg:grid-cols-2" aria-label={t("bench.new.models")}>
              {testable.map(row)}
            </ul>
            {tooBig.length > 0 ? (
              <details>
                <summary className="text-muted-foreground cursor-pointer text-sm">
                  {t("bench.new.tooBig", { count: tooBig.length })}
                </summary>
                <ul className="mt-1 grid gap-x-8 lg:grid-cols-2">{tooBig.map(row)}</ul>
              </details>
            ) : null}
          </>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor={imagesId} className="text-sm">
            {t("bench.new.images")}
          </label>
          <Select
            value={String(shown)}
            onValueChange={(value) => {
              setImages(Number(value));
            }}
          >
            <SelectTrigger id={imagesId} className="w-24">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {choices.map((count) => (
                <SelectItem key={count} value={String(count)}>
                  {count}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {overview.gpu ? (
            <span className="text-muted-foreground text-xs">
              {t("bench.new.gpu", {
                free: formatNumber(gigabytes(overview.gpu.free_mib), i18n.language, 1, gigabyte),
                total: formatNumber(gigabytes(overview.gpu.total_mib), i18n.language, 0, gigabyte),
              })}
            </span>
          ) : null}
        </div>
        <ul className="text-muted-foreground grid max-w-prose gap-1 text-xs">
          <li>
            {t("bench.new.settings", {
              context: formatNumber(overview.context_length, i18n.language),
              parallel: overview.parallel,
              language: t(`bench.language.${overview.language}`, {
                defaultValue: overview.language,
              }),
            })}
          </li>
          {overview.loaded.length > 0 && !active ? (
            <li>
              {t("bench.new.restore", {
                count: overview.loaded.length,
                names: overview.loaded.join(", "),
              })}
            </li>
          ) : null}
          {overview.running_jobs > 0 ? (
            <li>{t("bench.new.waiting", { count: overview.running_jobs })}</li>
          ) : null}
          <li>{t("bench.new.duration")}</li>
        </ul>
        {noFrames ? <p className="text-warning-ink text-sm">{t("bench.new.noFrames")}</p> : null}
        <div>
          <Button
            disabled={
              ticked.length === 0 ||
              noFrames ||
              active ||
              start.isPending ||
              Boolean(overview.lmstudio_error)
            }
            onClick={() => {
              start.mutate(
                { models: ticked, images: shown },
                {
                  onSuccess: (run) => {
                    toast.success(t("bench.new.started"));
                    onStarted(run.id);
                  },
                  onError: (error) => toast.error(errorMessage(error)),
                },
              );
            }}
          >
            {active
              ? t("bench.new.active")
              : ticked.length > 0
                ? t("bench.new.startCount", { count: ticked.length })
                : t("bench.new.start")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
