import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";

import { errorMessage, type ExportOption } from "@/api/client";
import { useExports } from "@/api/queries";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * « Exports »: one download per format with what it is for; a format the video
 * cannot give yet is disabled with the reason. Files are built by the server on request and never
 * written next to the video.
 */
export function ExportsTab({ videoId }: { videoId: string }) {
  const { t } = useTranslation();
  const exports = useExports(videoId);
  if (exports.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(exports.error)}</AlertDescription>
      </Alert>
    );
  }
  if (!exports.data) {
    return <Skeleton className="h-64 rounded-xl" />;
  }
  return (
    <section className="grid gap-3" aria-labelledby="exports-title">
      <div>
        <h2 id="exports-title" className="text-lg font-semibold">
          {t("exports.title")}
        </h2>
        <p className="text-muted-foreground text-sm">{t("exports.intro")}</p>
      </div>
      <ul className="grid gap-2 md:grid-cols-2">
        {exports.data.formats.map((option) => (
          <ExportRow key={option.format} option={option} />
        ))}
      </ul>
      <p className="text-muted-foreground text-xs">{t("exports.note")}</p>
    </section>
  );
}

function ExportRow({ option }: { option: ExportOption }) {
  const { t } = useTranslation();
  const label = t(`exports.format.${option.format}.label`);
  const hintId = `export-${option.format}-hint`;
  const reasonId = `export-${option.format}-reason`;
  const name = t("exports.downloadFormat", { format: label });
  return (
    <li className="bg-card flex items-start justify-between gap-3 rounded-xl border p-3">
      <div className="grid min-w-0 gap-1">
        <p className="font-medium">{label}</p>
        <p id={hintId} className="text-muted-foreground text-xs">
          {t(`exports.format.${option.format}.hint`)}
        </p>
        {option.available ? (
          <p className="text-muted-foreground truncate font-mono text-xs" title={option.filename}>
            {option.filename}
          </p>
        ) : (
          <p id={reasonId} className="text-warning text-xs">
            {option.reason ?? t("exports.unavailable")}
          </p>
        )}
      </div>
      {option.available ? (
        <Button size="sm" variant="outline" asChild>
          <a
            href={option.url}
            download={option.filename}
            aria-label={name}
            aria-describedby={hintId}
          >
            <Download aria-hidden />
            {t("exports.download")}
          </a>
        </Button>
      ) : (
        <Button size="sm" variant="outline" disabled aria-label={name} aria-describedby={reasonId}>
          <Download aria-hidden />
          {t("exports.download")}
        </Button>
      )}
    </li>
  );
}
