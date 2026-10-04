import { FileDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type SidecarResult, type SidecarStatus } from "@/api/client";
import { useExportSidecars } from "@/api/queries";
import { Button } from "@/components/ui/button";

import { selectVideos } from "./selection";

/**
 * Write the analysis file of the ticked videos now: « <name>.txt » next to each video, with all
 * its analyses and no image. A file of that name the app did not write is left alone.
 */
export function ExportButton({ ids }: { ids: readonly string[] }) {
  const { t } = useTranslation();
  const exportFiles = useExportSidecars();

  const report = (results: SidecarResult[]): void => {
    const count = (status: SidecarStatus): number =>
      results.filter((result) => result.status === status).length;
    const failed = results.filter((result) => result.status === "failed");
    const unknown = results.filter((result) => result.status === "unknown");
    selectVideos(
      unknown.map((result) => result.video_id),
      false,
    ); // removed from the library since they were ticked
    const details = [
      count("conflict") > 0 ? t("export.conflict", { count: count("conflict") }) : null,
      failed.length > 0
        ? t("export.failed", { count: failed.length, reason: failed[0]?.detail ?? "" })
        : null,
      count("not_analyzed") > 0 ? t("export.notAnalyzed", { count: count("not_analyzed") }) : null,
      count("offline") > 0 ? t("export.offline", { count: count("offline") }) : null,
      unknown.length > 0 ? t("batch.unknown", { count: unknown.length }) : null,
    ]
      .filter(Boolean)
      .join(" ");
    const options = details ? { description: details } : undefined;
    const written = count("written");
    if (written > 0) {
      toast.success(t("export.written", { count: written }), options);
    } else if (failed.length > 0) {
      toast.error(t("export.nothingWritten"), options);
    } else {
      toast.info(t("export.nothingWritten"), options);
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      size="xs"
      title={t("export.hint")}
      disabled={ids.length === 0 || exportFiles.isPending}
      onClick={() => {
        exportFiles.mutate([...ids], {
          onSuccess: (data) => {
            report(data.results);
          },
          onError: (error) => toast.error(errorMessage(error)),
        });
      }}
    >
      <FileDown aria-hidden />
      {t("export.button")}
    </Button>
  );
}
