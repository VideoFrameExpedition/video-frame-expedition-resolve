import {
  EllipsisVertical,
  FileVideo,
  FolderOpen,
  ListChecks,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type Root } from "@/api/client";
import { usePatchRoot, useRemoveRoot, useScanRoot } from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatDateTime } from "@/lib/format";

import { RootAnalyzeDialog } from "./RootAnalyzeDialog";
import { clearVideos } from "./selection";

function RootCard({ root }: { root: Root }) {
  const { t, i18n } = useTranslation();
  const scan = useScanRoot();
  const remove = useRemoveRoot();
  const patch = usePatchRoot();
  const [analyzeOpen, setAnalyzeOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  // Only the files a Resolve timeline brought in, never the rest of their folder:
  // no sub-folders and no exclusions to set.
  const files = root.kind === "files";
  const Icon = files ? FileVideo : FolderOpen;

  const onScan = (): void => {
    scan.mutate(root.id, {
      onSuccess: () => toast.success(t("library.scanQueued")),
      onError: (error) => toast.error(errorMessage(error)),
    });
  };
  const onRemove = (): void => {
    if (window.confirm(t("library.removeConfirm", { label: root.label }))) {
      // mutateAsync: this card unmounts once the folder is gone, and with it the callbacks
      // of a plain mutate(); the ticked videos may have gone with the folder.
      remove.mutateAsync(root.id).then(clearVideos, (error: unknown) => {
        toast.error(errorMessage(error));
      });
    }
  };

  return (
    <div className="glass flex min-w-64 flex-1 items-start gap-3 rounded-xl border p-4">
      <Icon className="text-brand-teal mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium" title={root.label}>
          {root.label}
        </p>
        <p className="text-muted-foreground truncate font-mono text-xs" title={root.path}>
          {root.path}
        </p>
        {files ? (
          <p className="text-muted-foreground text-xs" title={t("bins.filesRoot")}>
            {t("library.filesCount", { count: root.files_count ?? 0 })}
          </p>
        ) : null}
        <p className="text-muted-foreground mt-2 text-xs">
          {t("library.videoCount", { count: root.video_count })}
          {" · "}
          {t("library.readyCount", { count: root.ready_count })}
          {root.offline_count > 0
            ? ` · ${t("library.offlineCount", { count: root.offline_count })}`
            : ""}
        </p>
        {root.auto_analyze ? (
          <p className="text-brand-teal text-xs">{t("library.autoUpdateBadge")}</p>
        ) : null}
        <p className="text-muted-foreground text-xs">
          {root.last_scan_at
            ? t("library.lastScan", { date: formatDateTime(root.last_scan_at, i18n.language) })
            : t("library.neverScanned")}
        </p>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button ref={menuButton} variant="ghost" size="icon" aria-label={root.label}>
            <EllipsisVertical className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onSelect={() => {
              setAnalyzeOpen(true);
            }}
          >
            <ListChecks className="size-4" />
            {t("analysisMode.rootAction")}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onScan}>
            <RefreshCw className="size-4" />
            {t("library.scan")}
          </DropdownMenuItem>
          <DropdownMenuCheckboxItem
            checked={root.auto_analyze}
            disabled={patch.isPending}
            onCheckedChange={(on) => {
              patch.mutate(
                { rootId: root.id, body: { auto_analyze: on } },
                {
                  onSuccess: () => {
                    toast.success(t(on ? "library.autoUpdateOn" : "library.autoUpdateOff"));
                  },
                  onError: (error) => toast.error(errorMessage(error)),
                },
              );
            }}
          >
            {t("library.autoUpdate")}
          </DropdownMenuCheckboxItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={onRemove} className="text-destructive">
            <Trash2 className="size-4" />
            {t("library.remove")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <RootAnalyzeDialog
        root={root}
        open={analyzeOpen}
        onOpenChange={setAnalyzeOpen}
        returnFocusTo={menuButton}
      />
    </div>
  );
}

export function RootsPanel({ roots }: { roots: Root[] }) {
  return (
    <div className="flex flex-wrap gap-3">
      {roots.map((root) => (
        <RootCard key={root.id} root={root} />
      ))}
    </div>
  );
}
