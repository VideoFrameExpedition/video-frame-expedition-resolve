import { RotateCw } from "lucide-react";
import { useId, useState, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type AnalysisMode } from "@/api/client";
import { useAnalyze } from "@/api/queries";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export function AnalyzeDialog({
  videoId,
  missing,
  outdated,
}: {
  videoId: string;
  missing: string[];
  outdated: string[];
}) {
  const { t } = useTranslation();
  const ids = { focus: useId(), focusHint: useId() };
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState("");
  const [mode, setMode] = useState<AnalysisMode>("complete");
  const analyze = useAnalyze(videoId);
  const stageList = (names: string[]): string => names.map((name) => t(`stage.${name}`)).join(", ");

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    analyze.mutate(
      { mode, focus: focus.trim() || null },
      {
        onSuccess: () => {
          toast.success(t("video.queued"));
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
        <Button variant="secondary">
          <RotateCw className="size-4" />
          {t("video.analyze")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-5">
          <DialogHeader>
            <DialogTitle>{t("video.analyzeTitle")}</DialogTitle>
            <DialogDescription>{t("video.analyzeBody")}</DialogDescription>
          </DialogHeader>
          <div className="text-muted-foreground grid gap-1 text-sm">
            <p>
              {missing.length > 0
                ? t("analysisMode.missing", { stages: stageList(missing) })
                : t("analysisMode.nothingMissing")}
            </p>
            {outdated.length > 0 ? (
              <p>{t("analysisMode.outdated", { stages: stageList(outdated) })}</p>
            ) : null}
          </div>
          <AnalysisModeFields value={mode} onChange={setMode} />
          <div className="grid gap-2">
            <Label htmlFor={ids.focus}>{t("video.focus")}</Label>
            <Textarea
              id={ids.focus}
              aria-describedby={ids.focusHint}
              rows={3}
              value={focus}
              onChange={(e) => {
                setFocus(e.target.value);
              }}
            />
            <p id={ids.focusHint} className="text-muted-foreground text-xs">
              {t("video.focusHint")}
            </p>
          </div>
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
              {t(`analysisMode.submit.${mode}`)}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
