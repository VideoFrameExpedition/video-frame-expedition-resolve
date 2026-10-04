import { CircleStop } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { ACTIVE_JOBS_LIMIT, useActiveJobs, useCancelAllJobs } from "@/api/queries";
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
import { cn } from "@/lib/utils";

/** « Stop all »: shown only while jobs are queued or running. What is already analysed is
 * kept; « Analyse » takes up again where the analyses stopped. With ``withCount``, a line says
 * how many are active (library side panel). */
export function StopAllButton({
  withCount = false,
  className,
}: {
  withCount?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const active = useActiveJobs();
  const stop = useCancelAllJobs();
  const [open, setOpen] = useState(false);
  const count = active.data?.length ?? 0;
  if (count === 0) return null;
  const shown = count >= ACTIVE_JOBS_LIMIT ? `${String(ACTIVE_JOBS_LIMIT)}+` : String(count);

  const confirm = (): void => {
    stop.mutate(undefined, {
      onSuccess: (done) => {
        toast.success(t("jobs.stopped", { count: done.cancelled + done.stopping }));
        setOpen(false);
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {withCount ? (
        <span className="text-muted-foreground text-xs" aria-live="polite">
          {t("jobs.active", { count, shown })}
        </span>
      ) : null}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
          <Button variant="outline" size="sm" className="text-destructive hover:text-destructive">
            <CircleStop className="size-4" aria-hidden />
            {t("jobs.stopAll")}
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t("jobs.stopAllTitle", { count, shown })}</DialogTitle>
            <DialogDescription>{t("jobs.stopAllBody")}</DialogDescription>
          </DialogHeader>
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
            <Button type="button" variant="destructive" onClick={confirm} disabled={stop.isPending}>
              <CircleStop className="size-4" aria-hidden />
              {t("jobs.stopAllConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
