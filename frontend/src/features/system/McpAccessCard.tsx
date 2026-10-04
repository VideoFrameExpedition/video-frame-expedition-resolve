import { Bot } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";

import { useAnalysisSettings, usePatchAnalysisSettings } from "@/api/queries";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";

/** What Claude may do through the MCP server: add a new folder to the library with
 * analyze_folder only when allowed here (off by default: footage can carry instructions). */
export function McpAccessCard() {
  const { t } = useTranslation();
  const settings = useAnalysisSettings();
  const patch = usePatchAnalysisSettings();
  const id = useId();
  const hintId = useId();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Bot className="text-brand-teal size-4" aria-hidden />
          {t("system.mcp.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {settings.data ? (
          <div className="flex items-start justify-between gap-4">
            <div className="grid gap-1">
              <Label htmlFor={id}>{t("system.mcp.label")}</Label>
              <p id={hintId} className="text-muted-foreground text-xs">
                {t("system.mcp.hint")}
              </p>
            </div>
            <Switch
              id={id}
              aria-describedby={hintId}
              checked={settings.data.mcp_add_folders}
              disabled={patch.isPending}
              onCheckedChange={(checked) => {
                patch.mutate({ mcp_add_folders: checked });
              }}
            />
          </div>
        ) : (
          <Skeleton className="h-12" />
        )}
        <p className="text-muted-foreground text-xs">{t("system.mcp.note")}</p>
      </CardContent>
    </Card>
  );
}
