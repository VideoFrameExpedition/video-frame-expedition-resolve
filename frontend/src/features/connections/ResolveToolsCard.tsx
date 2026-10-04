import { Clapperboard } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { useAnalysisSettings, usePatchAnalysisSettings } from "@/api/queries";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

/** The assistant's DaVinci Resolve tools: read a timeline, plan a reframing, build new
 * timelines, put markers, through the application's fixed scripts. Off by default: they change
 * the project open in Resolve (new timelines only). */
export function ResolveToolsCard() {
  const { t } = useTranslation();
  const settings = useAnalysisSettings();
  const patch = usePatchAnalysisSettings();
  const id = useId();
  const hintId = useId();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Clapperboard className="text-brand-teal size-4" aria-hidden />
          {t("connections.resolveTools.title")}
        </CardTitle>
        <CardDescription>{t("connections.resolveTools.help")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {settings.data ? (
          <div className="flex items-start justify-between gap-4">
            <div className="grid gap-1">
              <Label htmlFor={id}>{t("connections.resolveTools.label")}</Label>
              <p id={hintId} className="text-muted-foreground text-xs">
                {t("connections.resolveTools.hint")}
              </p>
            </div>
            <Switch
              id={id}
              aria-describedby={hintId}
              checked={settings.data.mcp_resolve_tools}
              disabled={patch.isPending}
              onCheckedChange={(checked) => {
                patch.mutate({ mcp_resolve_tools: checked });
              }}
            />
          </div>
        ) : (
          <Skeleton className="h-12" />
        )}
        <p className="text-muted-foreground text-xs">{t("connections.resolveTools.note")}</p>
      </CardContent>
    </Card>
  );
}
