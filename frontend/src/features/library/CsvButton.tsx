import { FileSpreadsheet } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useExportCsv } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { saveBlob } from "@/lib/download";

/**
 * Download a table of the ticked videos, one row each: CSV for a French Excel
 * (UTF-8 with BOM, « ; », decimal comma). The server builds it; nothing is written on the disk.
 */
export function CsvButton({ ids }: { ids: readonly string[] }) {
  const { t } = useTranslation();
  const exportCsv = useExportCsv();
  return (
    <Button
      type="button"
      variant="outline"
      size="xs"
      title={t("exports.csv.hint")}
      aria-label={t("exports.csv.hint")}
      disabled={ids.length === 0 || exportCsv.isPending}
      onClick={() => {
        exportCsv.mutate([...ids], {
          onSuccess: ({ blob, filename }) => {
            saveBlob(blob, filename);
            toast.success(t("exports.csv.done", { count: ids.length }));
          },
          onError: (error) => toast.error(errorMessage(error)),
        });
      }}
    >
      <FileSpreadsheet aria-hidden />
      {t("exports.csv.button")}
    </Button>
  );
}
