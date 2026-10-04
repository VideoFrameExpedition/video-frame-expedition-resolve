import { ChevronRight, Play } from "lucide-react";
import { useId, useState, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type AnalysisMode, type BatchAnalysis, type StageInfo } from "@/api/client";
import { useAnalyzeVideos, useStages } from "@/api/queries";
import { AnalysisModeFields } from "@/components/AnalysisModeFields";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { StopAllButton } from "@/features/jobs/StopAllButton";
import { cn } from "@/lib/utils";

import {
  clearVideos,
  selectAllStages,
  selectStages,
  selectVideos,
  useSelectedVideos,
  useSkippedStages,
} from "./selection";
import { groupByFamily, hardDependents, requiredStages, type FamilyGroup } from "./stagePlan";

// Folded when the app starts (room for the bins); kept while the app stays open.
let stagesOpenAtStart = false;

/** Side panel of the library: analyse the ticked videos, for the ticked stages. */
export function AnalyzePanel() {
  const { t } = useTranslation();
  const heading = useId();
  const pickerId = useId();
  const [stagesOpen, setStagesOpen] = useState(stagesOpenAtStart);
  const stages = useStages();
  const selected = useSelectedVideos();
  const skipped = useSkippedStages();
  const all = stages.data ?? [];
  const chosen = all.filter((stage) => !skipped.has(stage.name)).map((stage) => stage.name);
  const required = requiredStages(all, new Set(chosen));

  return (
    <section aria-labelledby={heading} className="grid gap-5 border-t pt-5">
      <h2 id={heading} className="sr-only">
        {t("batch.heading")}
      </h2>
      <div className="grid gap-2">
        <BatchAnalyzeDialog
          videoIds={[...selected]}
          stages={all}
          chosen={chosen}
          required={all.map((stage) => stage.name).filter((name) => required.has(name))}
        />
        <p className="text-muted-foreground text-xs" aria-live="polite">
          {selected.size === 0 ? (
            t("batch.noVideo")
          ) : (
            <>
              {t("batch.selected", { count: selected.size })} ·{" "}
              <button
                type="button"
                onClick={clearVideos}
                className="hover:text-foreground focus-visible:ring-ring rounded underline underline-offset-2 focus-visible:ring-2 focus-visible:outline-none"
              >
                {t("batch.clear")}
              </button>
            </>
          )}
        </p>
        {selected.size > 0 && stages.data && chosen.length === 0 ? (
          <p className="text-warning text-xs">{t("batch.noStage")}</p>
        ) : null}
        {!stages.data && stages.isError ? (
          // In view even with the stage list folded: nothing can be analysed without it.
          <p className="text-destructive text-xs">{errorMessage(stages.error)}</p>
        ) : null}
        <StopAllButton withCount />
      </div>
      <button
        type="button"
        aria-expanded={stagesOpen}
        aria-controls={pickerId}
        onClick={() => {
          stagesOpenAtStart = !stagesOpen;
          setStagesOpen(!stagesOpen);
        }}
        className="hover:text-foreground focus-visible:ring-ring -mt-2 flex items-center gap-1.5 rounded text-left text-sm font-medium focus-visible:ring-2 focus-visible:outline-none"
      >
        <ChevronRight
          className={cn("size-4 transition-transform", stagesOpen && "rotate-90")}
          aria-hidden
        />
        {t("batch.stagesToggle", { chosen: chosen.length, total: all.length })}
      </button>
      {!stagesOpen ? null : stages.data ? (
        <div id={pickerId}>
          <StagePicker stages={all} skipped={skipped} required={required} />
        </div>
      ) : stages.isError ? null : (
        <Skeleton className="h-64 rounded-lg" />
      )}
    </section>
  );
}

function StagePicker({
  stages,
  skipped,
  required,
}: {
  stages: StageInfo[];
  skipped: ReadonlySet<string>;
  required: ReadonlySet<string>;
}) {
  const { t } = useTranslation();
  const names = stages.map((stage) => stage.name);
  const link =
    "hover:text-foreground focus-visible:ring-ring rounded underline underline-offset-2 focus-visible:ring-2 focus-visible:outline-none disabled:no-underline disabled:opacity-50";
  return (
    <fieldset className="grid gap-4">
      <legend className="mb-2 text-sm font-medium">{t("batch.stagesLegend")}</legend>
      <div className="text-muted-foreground -mt-2 flex gap-3 text-xs">
        <button
          type="button"
          className={link}
          disabled={names.every((name) => !skipped.has(name))}
          onClick={selectAllStages}
        >
          {t("batch.allStages")}
        </button>
        <button
          type="button"
          className={link}
          disabled={names.every((name) => skipped.has(name))}
          onClick={() => {
            selectStages(names, false);
          }}
        >
          {t("batch.noStages")}
        </button>
      </div>
      {groupByFamily(stages).map((group) => (
        <FamilyRows key={group.family} group={group} skipped={skipped} required={required} />
      ))}
    </fieldset>
  );
}

function FamilyRows({
  group,
  skipped,
  required,
}: {
  group: FamilyGroup;
  skipped: ReadonlySet<string>;
  required: ReadonlySet<string>;
}) {
  const { t } = useTranslation();
  const label = useId();
  const names = group.stages.map((stage) => stage.name);
  const ticked = names.filter((name) => !skipped.has(name)).length;
  return (
    <div role="group" aria-labelledby={label} className="grid gap-1.5">
      <label className="text-muted-foreground flex cursor-pointer items-center gap-2 text-xs font-semibold tracking-wide uppercase">
        <input
          type="checkbox"
          checked={ticked === names.length}
          ref={(input) => {
            if (input) input.indeterminate = ticked > 0 && ticked < names.length;
          }}
          onChange={(event) => {
            selectStages(names, event.target.checked);
          }}
          className="accent-primary size-3.5"
        />
        <span id={label}>{t(`batch.family.${group.family}`)}</span>
      </label>
      {group.stages.map((stage) => (
        <label
          key={stage.name}
          className="flex cursor-pointer items-start gap-2 pl-5 text-sm leading-snug"
        >
          <input
            type="checkbox"
            checked={!skipped.has(stage.name)}
            onChange={(event) => {
              selectStages([stage.name], event.target.checked);
            }}
            className="accent-primary mt-0.5 size-3.5 shrink-0"
          />
          <span>
            {t(`stage.${stage.name}`, { defaultValue: stage.name })}
            {required.has(stage.name) ? " " : null}
            {required.has(stage.name) ? (
              <span className="text-muted-foreground text-xs" title={t("batch.requiredHint")}>
                · {t("batch.required")}
              </span>
            ) : null}
          </span>
        </label>
      ))}
    </div>
  );
}

function BatchAnalyzeDialog({
  videoIds,
  stages,
  chosen,
  required,
}: {
  videoIds: string[];
  stages: StageInfo[];
  chosen: string[];
  required: string[];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<AnalysisMode>("complete");
  const analyze = useAnalyzeVideos();
  const every = chosen.length === stages.length;
  const redone = every ? [] : hardDependents(stages, new Set(chosen));
  const names = (list: string[]): string =>
    list.map((name) => t(`stage.${name}`, { defaultValue: name })).join(", ");

  const report = (result: BatchAnalysis): void => {
    selectVideos(result.unknown, false); // removed from the library since they were ticked
    const upToDate = every ? "batch.upToDateAll" : "batch.upToDate";
    const details = [
      result.up_to_date > 0 ? t(upToDate, { count: result.up_to_date }) : null,
      result.offline > 0 ? t("batch.offline", { count: result.offline }) : null,
      result.unknown.length > 0 ? t("batch.unknown", { count: result.unknown.length }) : null,
    ]
      .filter(Boolean)
      .join(" ");
    const options = details ? { description: details } : undefined;
    if (result.queued > 0) {
      toast.success(t("batch.queued", { count: result.queued }), options);
    } else {
      toast.info(t("batch.nothingQueued"), options);
    }
  };

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    analyze.mutate(
      { video_ids: videoIds, stages: every ? null : chosen, mode },
      {
        onSuccess: (result) => {
          report(result);
          setOpen(false);
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setMode("complete"); // never reopen on a heavier choice made earlier
      }}
    >
      <DialogTrigger asChild>
        <Button className="w-full" disabled={videoIds.length === 0 || chosen.length === 0}>
          <Play className="size-4" />
          {videoIds.length > 0
            ? t("batch.analyzeCount", { count: videoIds.length })
            : t("batch.analyze")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-5">
          <DialogHeader>
            <DialogTitle>{t("batch.title", { count: videoIds.length })}</DialogTitle>
            <DialogDescription>
              {every ? t("batch.stagesAll") : t("batch.stagesSome", { stages: names(chosen) })}
            </DialogDescription>
          </DialogHeader>
          {!every && required.length > 0 ? (
            <p className="text-muted-foreground text-sm">
              {t("batch.stagesRequired", { stages: names(required) })}
            </p>
          ) : null}
          <AnalysisModeFields value={mode} onChange={setMode} scope={every ? "all" : "chosen"} />
          {redone.length > 0 && mode !== "complete" ? (
            <p className="text-muted-foreground text-sm">
              {t(mode === "full" ? "batch.alsoRedone" : "batch.alsoRedoneIf", {
                stages: names(redone),
              })}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setOpen(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={analyze.isPending}>
              {every ? t(`analysisMode.submit.${mode}`) : t(`batch.submit.${mode}`)}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
