import { Info, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Alert, AlertDescription } from "@/components/ui/alert";

interface Part {
  status: string; // ready (or no_speech) when a result is stored
  run_status: string; // the latest run of the stage
  note: string | null;
}

/** Above a stored result: the stage is running again, or its latest run did not succeed. */
export function PartNotice({ part }: { part: Part }) {
  const { t } = useTranslation();
  if (part.run_status === "running") {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-xs" role="status">
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        {t("part.running")}
      </p>
    );
  }
  if (part.run_status === "failed" || part.run_status === "skipped") {
    return (
      <p className="text-muted-foreground text-xs" role="status">
        {t("part.previous", { reason: part.note ?? part.run_status })}
      </p>
    );
  }
  return null;
}

/** Nothing stored: why (running, failed, skipped, not analysed yet), with optional actions. */
export function PartMissing({
  part,
  prefix,
  children,
}: {
  part: Part;
  prefix: "audio" | "transcript";
  children?: React.ReactNode;
}) {
  const { t } = useTranslation();
  const running = part.status === "running";
  const text =
    part.status === "running"
      ? t("part.running")
      : part.status === "failed"
        ? t("part.failed", { reason: part.note ?? "" })
        : part.status === "not_run"
          ? t(`${prefix}.notRun`)
          : part.status === "no_speech"
            ? t("transcript.noSpeech")
            : t(`${prefix}.skipped`, { reason: part.note ?? "" });
  return (
    <Alert>
      {running ? <Loader2 className="animate-spin" aria-hidden /> : <Info aria-hidden />}
      <AlertDescription className="grid gap-3">
        <span role="status">{text}</span>
        {children}
      </AlertDescription>
    </Alert>
  );
}
