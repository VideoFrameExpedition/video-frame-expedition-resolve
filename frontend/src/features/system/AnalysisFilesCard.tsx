import { FileText } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { useAnalysisSettings, usePatchAnalysisSettings } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

/** The analysis file next to each video: written after every analysis unless turned
 * off here, taken over first when a video has no analysis yet. */
export function AnalysisFilesCard() {
  const { t } = useTranslation();
  const settings = useAnalysisSettings();
  const patch = usePatchAnalysisSettings();
  const id = useId();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FileText className="text-brand-teal size-4" aria-hidden />
          {t("system.analysisFiles.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {settings.data ? (
          <div className="flex items-start justify-between gap-4">
            <div className="grid gap-1">
              <Label htmlFor={id}>{t("system.analysisFiles.label")}</Label>
              <p className="text-muted-foreground text-xs">{t("system.analysisFiles.hint")}</p>
            </div>
            <Switch
              id={id}
              checked={settings.data.sidecar_files}
              disabled={patch.isPending}
              onCheckedChange={(checked) => {
                patch.mutate({ sidecar_files: checked });
              }}
            />
          </div>
        ) : (
          <Skeleton className="h-12" />
        )}
        <p className="text-muted-foreground text-xs">{t("system.analysisFiles.note")}</p>
      </CardContent>
    </Card>
  );
}
