import { Database, Download, RotateCcw, Upload } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { api, ApiError, errorMessage, type Schemas } from "@/api/client";
import {
  exportUrl,
  useCancelPendingData,
  useDataState,
  useExportData,
  useImportData,
  useResetData,
  useRestart,
} from "@/api/queries";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { downloadLink } from "@/lib/download";
import { formatBytes, formatDateTime } from "@/lib/format";
import { page } from "@/lib/page";

type DataState = Schemas["DataOut"];
type Pending = Schemas["PendingOut"];
type LastOperation = Schemas["LastOperationOut"];

const RESTART_POLL_MS = 1500;
const RESTART_PATIENCE_MS = 180_000;
const KNOWN_ERRORS = new Set(["not_a_library", "newer_library"]);

/** The database as a whole: exported (with the frames, at will), imported, or reset. An
 * import or a reset is carried out when the application starts again, which this card asks
 * for. */
export function DataCard() {
  const { t } = useTranslation();
  const data = useDataState();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Database className="text-brand-teal size-4" aria-hidden />
          {t("system.data.title")}
        </CardTitle>
        <CardDescription>{t("system.data.help")}</CardDescription>
      </CardHeader>
      <CardContent>
        {data.data ? (
          <DataBody data={data.data} />
        ) : data.isError ? (
          <p className="text-destructive text-sm">{errorMessage(data.error)}</p>
        ) : (
          <Skeleton className="h-48" />
        )}
      </CardContent>
    </Card>
  );
}

function DataBody({ data }: { data: DataState }) {
  const { t, i18n } = useTranslation();
  return (
    <div className="grid gap-6">
      <p className="text-muted-foreground text-sm">
        {t("system.data.sizes", {
          count: data.videos,
          database: formatBytes(data.database_bytes, i18n.language),
          media: formatBytes(data.media_bytes, i18n.language),
        })}
      </p>
      <ExportSection mediaBytes={data.media_bytes} />
      <ImportSection {...outcomeOf(data, "import")} />
      <ResetSection {...outcomeOf(data, "reset")} />
      <p className="text-muted-foreground text-xs break-words">
        {t("system.data.backupsDir", { path: data.backups_dir })}
      </p>
    </div>
  );
}

function ExportSection({ mediaBytes }: { mediaBytes: number }) {
  const { t, i18n } = useTranslation();
  const exportData = useExportData();
  const [images, setImages] = useState(true);
  const id = useId();
  const hintId = useId();
  const run = (): void => {
    exportData.mutate(images, {
      onSuccess: (prepared) => {
        toast.success(
          t("system.data.exported", { size: formatBytes(prepared.size_bytes, i18n.language) }),
        );
        downloadLink(exportUrl(prepared.token));
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };
  return (
    <section className="grid gap-3" aria-label={t("system.data.exportTitle")}>
      <h3 className="text-sm font-medium">{t("system.data.exportTitle")}</h3>
      <div className="flex items-start gap-2">
        <input
          id={id}
          type="checkbox"
          checked={images}
          aria-describedby={hintId}
          onChange={(event) => {
            setImages(event.target.checked);
          }}
          className="accent-primary mt-0.5 size-4"
        />
        <div className="grid gap-1">
          <Label htmlFor={id}>
            {t("system.data.withImages", { size: formatBytes(mediaBytes, i18n.language) })}
          </Label>
          <p id={hintId} className="text-muted-foreground text-xs">
            {t("system.data.withImagesHint")}
          </p>
        </div>
      </div>
      <p className="text-warning text-xs">{t("system.data.exportNote")}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={run} disabled={exportData.isPending}>
          <Download className="size-4" aria-hidden />
          {t("system.data.export")}
        </Button>
        <span role="status" className="text-muted-foreground text-xs">
          {exportData.isPending ? t("system.data.exporting") : null}
        </span>
      </div>
    </section>
  );
}

/** What waits for the next start, or what the last start did, shown under the action it
 * comes from: where the eyes are (the page keeps its place when it reloads after a restart). */
interface OutcomeProps {
  pending: Pending | null;
  last: LastOperation | null;
  canRestart: boolean;
}

function outcomeOf(data: DataState, action: Pending["action"]): OutcomeProps {
  return {
    pending: data.pending?.action === action ? data.pending : null,
    last: data.last?.action === action ? data.last : null,
    canRestart: data.can_restart,
  };
}

function Outcome({ pending, last, canRestart }: OutcomeProps) {
  if (pending) return <PendingNotice pending={pending} canRestart={canRestart} />;
  return last ? <LastNotice last={last} /> : null;
}

function ImportSection(outcome: OutcomeProps) {
  const { t } = useTranslation();
  const importData = useImportData();
  const [file, setFile] = useState<File | null>(null);
  const [keepSettings, setKeepSettings] = useState(true);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const fileId = useId();
  const fileHintId = useId();
  const keepId = useId();
  const keepHintId = useId();
  const confirm = (): void => {
    if (!file) return;
    importData.mutate(
      { file, keepSettings },
      {
        onSuccess: () => {
          setOpen(false);
          setFile(null);
          if (input.current) input.current.value = "";
          toast.success(t("system.data.importReady"));
        },
        onError: (error) => {
          setOpen(false);
          toast.error(dataError(error, t));
        },
      },
    );
  };
  return (
    <section className="grid gap-3" aria-label={t("system.data.importTitle")}>
      <h3 className="text-sm font-medium">{t("system.data.importTitle")}</h3>
      <div className="grid gap-1">
        <Label htmlFor={fileId}>{t("system.data.importFile")}</Label>
        <Input
          id={fileId}
          ref={input}
          type="file"
          accept=".zip,.sqlite3,application/zip"
          aria-describedby={fileHintId}
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
          }}
          className="max-w-md"
        />
        <p id={fileHintId} className="text-muted-foreground text-xs">
          {t("system.data.importHint")}
        </p>
      </div>
      <div className="flex items-start gap-2">
        <input
          id={keepId}
          type="checkbox"
          checked={keepSettings}
          aria-describedby={keepHintId}
          onChange={(event) => {
            setKeepSettings(event.target.checked);
          }}
          className="accent-primary mt-0.5 size-4"
        />
        <div className="grid gap-1">
          <Label htmlFor={keepId}>{t("system.data.keepSettings")}</Label>
          <p id={keepHintId} className="text-muted-foreground text-xs">
            {t("system.data.keepSettingsHint")}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button type="button" variant="outline" disabled={!file || importData.isPending}>
              <Upload className="size-4" aria-hidden />
              {t("system.data.import")}
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("system.data.importConfirmTitle")}</DialogTitle>
              <DialogDescription>
                {t("system.data.importConfirmBody", { name: file?.name ?? "" })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setOpen(false);
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button type="button" onClick={confirm} disabled={importData.isPending}>
                <Upload className="size-4" aria-hidden />
                {t("system.data.importConfirm")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <span role="status" className="text-muted-foreground text-xs">
          {importData.isPending ? t("system.data.importing") : null}
        </span>
      </div>
      <Outcome {...outcome} />
    </section>
  );
}

function ResetSection(outcome: OutcomeProps) {
  const { t } = useTranslation();
  const reset = useResetData();
  const [library, setLibrary] = useState(true);
  const [settings, setSettings] = useState(false);
  const [open, setOpen] = useState(false);
  const libraryId = useId();
  const settingsId = useId();
  const what = library && settings ? "Both" : library ? "Library" : "Settings";
  const confirm = (): void => {
    reset.mutate(
      { library, settings },
      {
        onSuccess: () => {
          setOpen(false);
          toast.success(t("system.data.resetReady"));
        },
        onError: (error) => {
          setOpen(false);
          toast.error(errorMessage(error));
        },
      },
    );
  };
  return (
    <section className="grid gap-3" aria-label={t("system.data.resetTitle")}>
      <h3 className="text-sm font-medium">{t("system.data.resetTitle")}</h3>
      <div className="grid gap-2">
        <label htmlFor={libraryId} className="flex items-center gap-2 text-sm">
          <input
            id={libraryId}
            type="checkbox"
            checked={library}
            onChange={(event) => {
              setLibrary(event.target.checked);
            }}
            className="accent-primary size-4"
          />
          {t("system.data.resetLibrary")}
        </label>
        <label htmlFor={settingsId} className="flex items-center gap-2 text-sm">
          <input
            id={settingsId}
            type="checkbox"
            checked={settings}
            onChange={(event) => {
              setSettings(event.target.checked);
            }}
            className="accent-primary size-4"
          />
          {t("system.data.resetSettings")}
        </label>
        <p className="text-muted-foreground text-xs">{t("system.data.resetHint")}</p>
      </div>
      <div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button
              type="button"
              variant="outline"
              className="text-destructive hover:text-destructive"
              disabled={!(library || settings) || reset.isPending}
            >
              <RotateCcw className="size-4" aria-hidden />
              {t("system.data.reset")}
            </Button>
          </DialogTrigger>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>{t("system.data.resetConfirmTitle")}</DialogTitle>
              <DialogDescription>
                {t(`system.data.resetConfirm${what}`)}{" "}
                {t(library ? "system.data.resetConfirmBackup" : "system.data.resetConfirmCopy")}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setOpen(false);
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                variant="destructive"
                onClick={confirm}
                disabled={reset.isPending}
              >
                <RotateCcw className="size-4" aria-hidden />
                {t("system.data.resetConfirm")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
      <Outcome {...outcome} />
    </section>
  );
}

function PendingNotice({ pending, canRestart }: { pending: Pending; canRestart: boolean }) {
  const { t } = useTranslation();
  const restart = useRestart();
  const cancel = useCancelPendingData();
  const [askedAt, setAskedAt] = useState<number | null>(null);
  const [late, setLate] = useState(false);

  useEffect(() => {
    if (askedAt === null) return;
    // Back when the new start has carried out what was prepared (nothing pending any more).
    const timer = window.setInterval(() => {
      if (Date.now() - askedAt > RESTART_PATIENCE_MS) setLate(true);
      api.GET("/api/v1/system/data").then(
        ({ data }) => {
          if (data && !data.pending) page.reload();
        },
        () => undefined, // stopped for now
      );
    }, RESTART_POLL_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [askedAt]);

  const ask = (): void => {
    restart.mutate(undefined, {
      onSuccess: () => {
        setAskedAt(Date.now());
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  return (
    <Alert className="border-brand-teal/60 [&>svg]:text-brand-teal">
      <RotateCcw aria-hidden />
      <AlertDescription className="grid gap-2">
        <p className="text-foreground font-medium">
          {t(pending.action === "import" ? "system.data.importReady" : "system.data.resetReady")}
        </p>
        <p>
          {pendingSummary(pending, t)} {t("system.data.pendingWhen")}
        </p>
        {askedAt !== null ? (
          <p role="status" className={late ? "text-warning" : "text-brand-teal"}>
            {t(late ? "system.data.restartLate" : "system.data.restarting")}
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            {canRestart ? (
              <Button type="button" size="sm" onClick={ask} disabled={restart.isPending}>
                <RotateCcw className="size-4" aria-hidden />
                {t("system.data.restartNow")}
              </Button>
            ) : (
              <span>{t("system.data.restartManual")}</span>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                cancel.mutate(undefined, {
                  onError: (error) => toast.error(errorMessage(error)),
                });
              }}
              disabled={cancel.isPending || restart.isPending}
            >
              {t("system.data.cancelPending")}
            </Button>
          </div>
        )}
        {canRestart && askedAt === null ? (
          <p className="text-xs">{t("system.data.restartHint")}</p>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

function LastNotice({ last }: { last: LastOperation }) {
  const { t, i18n } = useTranslation();
  const when = formatDateTime(last.done_at, i18n.language);
  if (last.error) {
    return (
      <p className="text-destructive text-sm">
        {t("system.data.lastFailed", { when, error: last.error })}
      </p>
    );
  }
  return (
    <p className="text-sm">
      {t(last.action === "import" ? "system.data.lastImport" : "system.data.lastReset", { when })}
      {last.backup ? ` ${t("system.data.lastBackup", { file: last.backup })}` : ""}
    </p>
  );
}

type Translate = ReturnType<typeof useTranslation>["t"];

function pendingSummary(pending: Pending, t: Translate): string {
  if (pending.action === "reset") {
    const what =
      pending.library && pending.settings ? "Both" : pending.library ? "Library" : "Settings";
    return t(`system.data.pendingReset${what}`);
  }
  return [
    t("system.data.pendingImport", { name: pending.source ?? "", count: pending.videos ?? 0 }),
    t(pending.images ? "system.data.pendingImages" : "system.data.pendingNoImages"),
    t(pending.settings ? "system.data.pendingTheirSettings" : "system.data.pendingKeepSettings"),
  ].join(" ");
}

function dataError(error: unknown, t: Translate): string {
  if (error instanceof ApiError && KNOWN_ERRORS.has(error.code)) {
    return t(`system.data.errors.${error.code}`);
  }
  return errorMessage(error);
}
