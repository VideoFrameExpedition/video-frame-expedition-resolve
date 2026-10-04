import { useState, type RefObject, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type AnalysisMode, type Root } from "@/api/client";
import { useAnalyzeRoot } from "@/api/queries";
import { AnalysisModeFields } from "@/components/AnalysisModeFields";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function RootAnalyzeDialog({
  root,
  open,
  onOpenChange,
  returnFocusTo,
}: {
  root: Root;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Opened from a menu item: focus goes back to the menu's button when the dialog closes. */
  returnFocusTo?: RefObject<HTMLButtonElement | null>;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<AnalysisMode>("complete");
  const analyze = useAnalyzeRoot();

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    analyze.mutate(
      { rootId: root.id, mode },
      {
        onSuccess: ({ queued }) => {
          if (queued === 0) toast.info(t("analysisMode.rootNothing"));
          else toast.success(t("analysisMode.rootQueued", { count: queued }));
          onOpenChange(false);
          setMode("complete");
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setMode("complete"); // opened by its parent: reset when it closes
      }}
    >
      <DialogContent
        onCloseAutoFocus={(event) => {
          if (returnFocusTo?.current) {
            event.preventDefault();
            returnFocusTo.current.focus();
          }
        }}
      >
        <form onSubmit={submit} className="grid gap-5">
          <DialogHeader>
            <DialogTitle>{t("analysisMode.rootTitle", { label: root.label })}</DialogTitle>
            <DialogDescription>{t("analysisMode.rootBody")}</DialogDescription>
          </DialogHeader>
          <AnalysisModeFields value={mode} onChange={setMode} />
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                onOpenChange(false);
                setMode("complete");
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
