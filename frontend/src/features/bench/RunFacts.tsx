import { Pencil } from "lucide-react";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type BenchRun } from "@/api/client";
import { useAnnotateBench } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatNumber } from "@/lib/format";

import { isActive } from "./benchFormat";

const MAX_NOTE = 300;

/** The user's words about a run (shown in the history), written or changed in place. */
function RunNote({ run }: { run: BenchRun }) {
  const { t } = useTranslation();
  const annotate = useAnnotateBench();
  const field = useId();
  const [draft, setDraft] = useState<string>();
  if (draft === undefined) {
    return (
      <p className="flex flex-wrap items-center gap-2 text-sm">
        {run.note ? <span>{run.note}</span> : null}
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground h-7 px-2"
          onClick={() => {
            setDraft(run.note ?? "");
          }}
        >
          <Pencil className="size-3.5" />
          {t(run.note ? "bench.note.edit" : "bench.note.add")}
        </Button>
      </p>
    );
  }
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        annotate.mutate(
          { runId: run.id, note: draft.trim() || null },
          {
            onSuccess: () => {
              setDraft(undefined);
            },
            onError: (error) => toast.error(errorMessage(error)),
          },
        );
      }}
    >
      <label htmlFor={field} className="sr-only">
        {t("bench.note.label")}
      </label>
      <Input
        id={field}
        value={draft}
        maxLength={MAX_NOTE}
        placeholder={t("bench.note.placeholder")}
        className="max-w-xl flex-1"
        onChange={(event) => {
          setDraft(event.target.value);
        }}
      />
      <Button type="submit" size="sm" disabled={annotate.isPending}>
        {t("bench.note.save")}
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => {
          setDraft(undefined);
        }}
      >
        {t("bench.note.cancel")}
      </Button>
    </form>
  );
}

/** How a run was done, its note, and whether the model loaded before it came back. */
export function RunFacts({ run }: { run: BenchRun }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="grid gap-1.5">
      <p className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="secondary">{t(`bench.status.${run.status}`)}</Badge>
        {t("bench.results.facts", {
          images: run.images,
          language: t(`bench.language.${run.language}`, { defaultValue: run.language }),
          context: formatNumber(run.context_length, i18n.language),
          parallel: run.parallel,
        })}
      </p>
      <RunNote key={run.id} run={run} />
      {run.error ? <p className="text-destructive text-sm">{run.error}</p> : null}
      {isActive(run.status)
        ? null
        : run.previous.map((model) =>
            model.restored === false ? (
              <p key={model.instance_id} className="text-warning-ink text-sm">
                {t("bench.results.notRestored", { name: model.display_name, error: model.error })}
              </p>
            ) : model.restored ? (
              <p key={model.instance_id} className="text-muted-foreground text-xs">
                {t("bench.results.restored", { name: model.display_name })}
              </p>
            ) : null,
          )}
    </div>
  );
}
