import { Cpu } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { useAnalysisSettings, usePatchAnalysisSettings } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

type Setting = "gpu_decode" | "gpu_transcription";

/** What may borrow the GPU when the vision model leaves enough memory free. */
export function HardwareCard() {
  const { t } = useTranslation();
  const settings = useAnalysisSettings();
  const patch = usePatchAnalysisSettings();
  const ids = { gpu_decode: useId(), gpu_transcription: useId() };
  const rows: Setting[] = ["gpu_decode", "gpu_transcription"];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Cpu className="text-brand-teal size-4" aria-hidden />
          {t("system.hardware.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {settings.data ? (
          rows.map((key) => (
            <div key={key} className="flex items-start justify-between gap-4">
              <div className="grid gap-1">
                <Label htmlFor={ids[key]}>{t(`system.hardware.${key}`)}</Label>
                <p className="text-muted-foreground text-xs">{t(`system.hardware.${key}Hint`)}</p>
              </div>
              <Switch
                id={ids[key]}
                checked={settings.data[key]}
                disabled={patch.isPending}
                onCheckedChange={(checked) => {
                  patch.mutate({ [key]: checked });
                }}
              />
            </div>
          ))
        ) : (
          <Skeleton className="h-20" />
        )}
        <p className="text-muted-foreground text-xs">{t("system.hardware.note")}</p>
      </CardContent>
    </Card>
  );
}
