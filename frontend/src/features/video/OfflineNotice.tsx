import { useNavigate } from "@tanstack/react-router";
import { FileX } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { VideoDetail } from "@/api/client";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { OfflineActions } from "@/features/library/OfflineActions";

/** The video's file is no longer where the library knew it: relink it, or take it out. */
export function OfflineNotice({ video }: { video: VideoDetail }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <Alert>
      <FileX className="size-4" />
      <AlertTitle>{t("offline.title")}</AlertTitle>
      <AlertDescription className="grid gap-3">
        <p className="break-all">{t("offline.body", { path: video.path })}</p>
        <OfflineActions
          ids={[video.id]}
          onForgotten={() => {
            void navigate({ to: "/library" });
          }}
        />
      </AlertDescription>
    </Alert>
  );
}
