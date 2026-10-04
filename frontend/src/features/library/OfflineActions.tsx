import { FolderSearch, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useForgetVideos, usePickFolder, useRelinkVideos } from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

import { selectVideos } from "./selection";

function onError(error: unknown): void {
  toast.error(errorMessage(error));
}

/** What can be done with offline videos (their files are no longer where the library knew
 * them): « Relink… », as DaVinci Resolve's Relink, or take them out of the library. */
export function OfflineActions({ ids, onForgotten }: { ids: string[]; onForgotten?: () => void }) {
  const { t } = useTranslation();
  const [confirming, setConfirming] = useState(false);
  const picker = usePickFolder();
  const relink = useRelinkVideos();
  const forget = useForgetVideos();
  const count = ids.length;

  // The Windows folder dialog opens on this computer; the search runs as a task.
  const relinkTo = (): void => {
    picker.mutate(undefined, {
      onSuccess: ({ path }) => {
        if (!path) return;
        relink.mutate(
          { video_ids: ids, folder: path },
          {
            onSuccess: () => toast.success(t("offline.relinkStarted", { count, folder: path })),
            onError,
          },
        );
      },
      onError,
    });
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        onClick={relinkTo}
        disabled={picker.isPending || relink.isPending}
        title={t("offline.relinkHint")}
      >
        <FolderSearch className="size-4" />
        {t("offline.relink", { count })}
      </Button>
      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogTrigger asChild>
          <Button variant="ghost" size="sm" className="text-destructive">
            <Trash2 className="size-4" />
            {t("offline.forget", { count })}
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("offline.forgetTitle", { count })}</DialogTitle>
            <DialogDescription>{t("offline.forgetBody")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => {
                setConfirming(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={forget.isPending}
              onClick={() => {
                forget.mutate(ids, {
                  onSuccess: ({ forgotten, left }) => {
                    setConfirming(false);
                    selectVideos(ids, false); // gone from the library: no longer ticked
                    toast.success(t("offline.forgotten", { count: forgotten }));
                    if (left > 0) toast.info(t("offline.left", { count: left }));
                    onForgotten?.();
                  },
                  onError,
                });
              }}
            >
              {t("offline.forgetConfirm", { count })}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
