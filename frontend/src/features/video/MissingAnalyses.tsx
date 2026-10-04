import { ListPlus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type VideoDetail } from "@/api/client";
import { useAnalyze } from "@/api/queries";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** Parts an analysed video still lacks (added by an update, failed, skipped for now). */
export function MissingAnalyses({ detail }: { detail: VideoDetail }) {
  const { t } = useTranslation();
  const analyze = useAnalyze(detail.id);
  const settled = detail.status === "ready" || detail.status === "partial";
  if (!settled || detail.missing_stages.length === 0) return null;
  const stages = detail.missing_stages.map((name) => t(`stage.${name}`)).join(", ");

  return (
    <Alert>
      <ListPlus aria-hidden />
      <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
        <span>{t("analysisMode.banner", { stages })}</span>
        <Button
          size="sm"
          variant="secondary"
          disabled={analyze.isPending}
          onClick={() => {
            analyze.mutate(
              { mode: "complete" },
              {
                onSuccess: () => toast.success(t("video.queued")),
                onError: (error) => toast.error(errorMessage(error)),
              },
            );
          }}
        >
          {t("analysisMode.submit.complete")}
        </Button>
      </AlertDescription>
    </Alert>
  );
}
