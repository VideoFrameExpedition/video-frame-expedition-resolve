import { Languages } from "lucide-react";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useAnalysisSettings, usePatchAnalysisSettings, useTranslateLibrary } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

/** The analyses in French and in English: the language the models write them in,
 * the other one by translation, and the translation of the analyses already made. */
export function LanguagesCard() {
  const { t } = useTranslation();
  const settings = useAnalysisSettings();
  const patch = usePatchAnalysisSettings();
  const translate = useTranslateLibrary();
  const id = useId();
  const hintId = useId();
  const written = settings.data?.language;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Languages className="text-brand-teal size-4" aria-hidden />
          {t("system.languages.title")}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        <p className="text-muted-foreground text-xs">{t("system.languages.intro")}</p>
        {written ? (
          <div className="flex items-start justify-between gap-4">
            <div className="grid gap-1">
              <Label htmlFor={id}>{t("system.languages.written")}</Label>
              <p id={hintId} className="text-muted-foreground text-xs">
                {t("system.languages.writtenHint")}
              </p>
            </div>
            <Select
              value={written}
              disabled={patch.isPending}
              onValueChange={(value) => {
                patch.mutate(
                  { language: value },
                  { onError: (error) => toast.error(errorMessage(error)) },
                );
              }}
            >
              <SelectTrigger id={id} className="w-36" aria-describedby={hintId}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fr">Français</SelectItem>
                <SelectItem value="en">English</SelectItem>
              </SelectContent>
            </Select>
          </div>
        ) : (
          <Skeleton className="h-12" />
        )}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <p className="text-muted-foreground max-w-prose text-xs">
            {t("system.languages.translateHint")}
          </p>
          <Button
            variant="secondary"
            disabled={translate.isPending}
            onClick={() => {
              translate.mutate(undefined, {
                onSuccess: (result) => {
                  if (result.queued > 0) {
                    toast.success(t("system.languages.queued", { count: result.queued }));
                  } else {
                    toast.info(t("system.languages.nothing"));
                  }
                },
                onError: (error) => toast.error(errorMessage(error)),
              });
            }}
          >
            {t("system.languages.translate")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
