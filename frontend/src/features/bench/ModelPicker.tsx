import {
  Clock,
  FlaskConical,
  Hourglass,
  Languages,
  MemoryStick,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { useId, useState, type ReactNode } from "react";
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
import { FamilyDot, ParamsChip, QuantChip } from "./CodeBadges";
import {
  familyOf,
  modelTree,
  passes,
  underSpot,
  type ModelFamily,
  type ModelFilters,
  type TreeSpot,
} from "./modelCodes";
import { ModelTree } from "./ModelTree";

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

/** One thing to know before the launch, with its icon. */
function Fact({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2.5">
      <Icon className="text-brand-teal mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </li>
  );
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
            <FamilyDot family={familyOf(model)} />
            <span className="font-medium">{model.display_name}</span>
            <ParamsChip params={model.params} />
            <QuantChip quantization={model.quantization} />
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

/** The models under a place of the tree, in groups of the level below it: by family for every
 * model, by folder for a family, a single group for a folder. */
function groupsOf(
  families: ModelFamily[],
  spot: TreeSpot,
): { id: string; family?: string; folder?: string; models: BenchModel[] }[] {
  if (spot.family === undefined) {
    return families.map((family) => ({
      id: `family:${family.name}`,
      family: family.name,
      models: family.folders.flatMap((folder) => folder.models),
    }));
  }
  const folders = families.flatMap((family) => family.folders);
  if (spot.folder === undefined) {
    return folders.map((folder) => ({
      id: `folder:${folder.name}`,
      folder: folder.name,
      models: folder.models,
    }));
  }
  return [{ id: "spot", models: folders.flatMap((folder) => folder.models) }];
}

/** « Run a test »: the vision models of LM Studio, found in a tree of its folders or all
 * together, to tick, and the launch. */
export function ModelPicker({
  overview,
  filters,
  onStarted,
}: {
  overview: BenchOverview;
  filters: ModelFilters; // those of the page: only the models they keep are shown
  onStarted: (runId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const start = useStartBench();
  const imagesId = useId();
  const available = new Set(
    overview.models.filter((model) => model.fit !== "too_big").map((model) => model.key),
  );
  const [selected, setSelected] = useState<string[]>(storedSelection);
  const [chosenSpot, setSpot] = useState<TreeSpot>({});
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

  const kept = overview.models.filter((model) => passes(model, filters));
  const tree = modelTree(kept);
  const keptKeys = new Set(kept.map((model) => model.key));
  const hiddenTicked = ticked.filter((key) => !keptKeys.has(key)).length;
  // A family or a folder LM Studio no longer holds: every model again.
  const spot = underSpot(tree, chosenSpot).length > 0 ? chosenSpot : {};
  const families = underSpot(tree, spot);
  const groups = groupsOf(families, spot);
  const here = groups.flatMap((group) => group.models);
  const hereTestable = here.filter((model) => model.fit !== "too_big").map((model) => model.key);
  const tooBig = here.filter((model) => model.fit === "too_big");

  const keep = (next: string[]): void => {
    setSelected(next);
    storeSelection(next);
  };
  const toggle = (key: string, checked: boolean): void => {
    keep(checked ? [...ticked, key] : ticked.filter((other) => other !== key));
  };
  const tickHere = (): void => {
    const wanted = hereTestable.filter((key) => !ticked.includes(key));
    const room = Math.max(0, overview.max_models - ticked.length);
    if (wanted.length > room) {
      toast.info(t("bench.new.max", { max: overview.max_models }));
    }
    keep([...ticked, ...wanted.slice(0, room)]);
  };
  const untickHere = (): void => {
    keep(ticked.filter((key) => !hereTestable.includes(key)));
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
  const place =
    spot.family === undefined
      ? t("bench.tree.all")
      : [spot.family || t("bench.tree.noFamily"), spot.folder].filter(Boolean).join(" › ");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FlaskConical className="text-brand-teal size-4" aria-hidden />
          {t("bench.new.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-6">
        <p className="text-muted-foreground text-sm leading-relaxed">{t("bench.new.intro")}</p>
        {overview.lmstudio_error ? (
          <p className="text-destructive text-sm">{t("bench.new.unreachable")}</p>
        ) : overview.models.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("bench.new.noModels")}</p>
        ) : kept.length === 0 ? (
          <p className="text-muted-foreground text-sm">{t("bench.filters.none")}</p>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(13rem,17rem)_minmax(0,1fr)]">
              <div className="self-start rounded-xl border p-1.5 md:sticky md:top-4 md:max-h-[calc(100vh-2rem)] md:overflow-y-auto">
                <ModelTree tree={tree} spot={spot} ticked={new Set(ticked)} onChoose={setSpot} />
              </div>
              <div className="grid min-w-0 grid-cols-1 content-start gap-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <h3 className="text-sm font-medium">{place}</h3>
                  <span className="text-muted-foreground text-xs">
                    {t("bench.tree.count", { count: here.length })}
                  </span>
                  {spot.family !== undefined && hereTestable.length > 0 ? (
                    <span className="ml-auto flex gap-2">
                      {hereTestable.some((key) => !ticked.includes(key)) ? (
                        <Button variant="secondary" size="sm" onClick={tickHere}>
                          {t("bench.tree.tickHere", { count: hereTestable.length })}
                        </Button>
                      ) : null}
                      {hereTestable.some((key) => ticked.includes(key)) ? (
                        <Button variant="ghost" size="sm" onClick={untickHere}>
                          {t("bench.tree.untickHere")}
                        </Button>
                      ) : null}
                    </span>
                  ) : null}
                </div>
                {groups.map((group) => {
                  const testable = group.models.filter((model) => model.fit !== "too_big");
                  if (testable.length === 0) return null;
                  return (
                    <section key={group.id} className="grid grid-cols-1 gap-1">
                      {group.family !== undefined ? (
                        <h4 className="flex items-center gap-2 text-xs font-semibold tracking-wide uppercase">
                          <FamilyDot family={group.family} />
                          {group.family || t("bench.tree.noFamily")}
                        </h4>
                      ) : group.folder !== undefined ? (
                        <h4 className="text-muted-foreground font-mono text-xs">{group.folder}</h4>
                      ) : null}
                      <ul
                        className="grid grid-cols-1 gap-x-8 xl:grid-cols-2"
                        aria-label={t("bench.new.models")}
                      >
                        {testable.map(row)}
                      </ul>
                    </section>
                  );
                })}
                {tooBig.length > 0 ? (
                  <details>
                    <summary className="text-muted-foreground cursor-pointer text-sm">
                      {t("bench.new.tooBig", { count: tooBig.length })}
                    </summary>
                    <ul className="mt-1 grid grid-cols-1 gap-x-8 xl:grid-cols-2">
                      {tooBig.map(row)}
                    </ul>
                  </details>
                ) : null}
              </div>
            </div>
          </>
        )}
        <section
          aria-label={t("bench.new.launch")}
          className="bg-secondary/30 grid gap-4 rounded-xl border p-4 md:p-5"
        >
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <div className="flex items-center gap-3">
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
            </div>
            {overview.gpu ? (
              <span className="text-muted-foreground flex items-center gap-2 text-sm">
                <MemoryStick className="size-4 shrink-0" aria-hidden />
                {t("bench.new.gpu", {
                  free: formatNumber(gigabytes(overview.gpu.free_mib), i18n.language, 1, gigabyte),
                  total: formatNumber(
                    gigabytes(overview.gpu.total_mib),
                    i18n.language,
                    0,
                    gigabyte,
                  ),
                })}
              </span>
            ) : null}
            <Button
              className="ml-auto"
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
          {noFrames ? <p className="text-warning-ink text-sm">{t("bench.new.noFrames")}</p> : null}
          {hiddenTicked > 0 ? (
            <p className="text-muted-foreground text-sm">
              {t("bench.filters.hiddenTicked", { count: hiddenTicked })}
            </p>
          ) : null}
          <ul className="text-muted-foreground grid gap-x-10 gap-y-2.5 border-t pt-4 text-sm md:grid-cols-2">
            <Fact icon={SlidersHorizontal}>
              {t("bench.new.load", {
                context: formatNumber(overview.context_length, i18n.language),
                parallel: overview.parallel,
              })}
            </Fact>
            <Fact icon={Languages}>
              {t("bench.new.language", {
                language: t(`bench.language.${overview.language}`, {
                  defaultValue: overview.language,
                }),
              })}
            </Fact>
            <Fact icon={Clock}>{t("bench.new.duration")}</Fact>
            <Fact icon={ShieldCheck}>{t("bench.new.untouched")}</Fact>
            {overview.loaded.length > 0 && !active ? (
              <Fact icon={RotateCcw}>
                {t("bench.new.restore", {
                  count: overview.loaded.length,
                  names: overview.loaded.join(", "),
                })}
              </Fact>
            ) : null}
            {overview.running_jobs > 0 ? (
              <Fact icon={Hourglass}>
                {t("bench.new.waiting", { count: overview.running_jobs })}
              </Fact>
            ) : null}
          </ul>
        </section>
      </CardContent>
    </Card>
  );
}
