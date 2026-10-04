import { CircleAlert, CircleCheck, RefreshCw, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "@/api/client";
import { useDoctor, useLmModels } from "@/api/queries";
import { PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes } from "@/lib/format";

import { AnalysisFilesCard } from "./AnalysisFilesCard";
import { HardwareCard } from "./HardwareCard";
import { LanguagesCard } from "./LanguagesCard";
import { LmStudioCard } from "./LmStudioCard";
import { McpAccessCard } from "./McpAccessCard";
import { VisionCard } from "./VisionCard";

function CheckIcon({ status }: { status: Schemas["CheckStatus"] }) {
  if (status === "ok") {
    return <CircleCheck className="text-success size-5 shrink-0" aria-hidden />;
  }
  if (status === "warning") {
    return <TriangleAlert className="text-warning size-5 shrink-0" aria-hidden />;
  }
  return <CircleAlert className="text-destructive size-5 shrink-0" aria-hidden />;
}

export function SystemPage() {
  const { t, i18n } = useTranslation();
  const doctor = useDoctor();
  const models = useLmModels();
  return (
    <div className="grid gap-6">
      <PageHeader
        title={t("system.title")}
        subtitle={
          doctor.data
            ? `${t("system.subtitle")} · v${doctor.data.version} · Python ${doctor.data.python}`
            : t("system.subtitle")
        }
        actions={
          <Button
            variant="secondary"
            onClick={() => void doctor.refetch()}
            disabled={doctor.isFetching}
          >
            <RefreshCw className={doctor.isFetching ? "size-4 animate-spin" : "size-4"} />
            {t("system.run")}
          </Button>
        }
      />
      {doctor.data ? (
        <ul className="grid gap-3">
          {doctor.data.checks.map((check) => (
            <li key={check.id} className="bg-card flex items-start gap-3 rounded-xl border p-4">
              <CheckIcon status={check.status} />
              <div className="min-w-0">
                <p className="font-medium">
                  {check.label}{" "}
                  <span className="text-muted-foreground text-xs font-normal">
                    · {t(`system.${check.status}`)}
                  </span>
                </p>
                <p className="text-sm break-words">{check.detail}</p>
                {check.hint ? (
                  <p className="text-muted-foreground mt-1 text-xs">{check.hint}</p>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <Skeleton className="h-64 rounded-xl" />
      )}
      <LmStudioCard />
      <VisionCard />
      <HardwareCard />
      <LanguagesCard />
      <AnalysisFilesCard />
      <McpAccessCard />
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("system.models")}</CardTitle>
        </CardHeader>
        <CardContent>
          {models.isError ? (
            <p className="text-destructive text-sm">{t("lmstudio.offline")}</p>
          ) : (
            <ul className="divide-border divide-y">
              {(models.data ?? []).map((model) => (
                <li
                  key={model.key}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
                >
                  <span className="min-w-0">
                    <span className="font-medium">{model.display_name}</span>{" "}
                    <span className="text-muted-foreground font-mono text-xs">{model.key}</span>
                  </span>
                  <span className="flex items-center gap-1.5">
                    {model.vision ? <Badge variant="secondary">{t("system.vision")}</Badge> : null}
                    {model.type === "embedding" ? (
                      <Badge variant="secondary">{t("system.embedding")}</Badge>
                    ) : null}
                    {model.loaded_instances.length > 0 ? (
                      <Badge className="bg-accent text-accent-foreground border-0">
                        {t("system.loaded")}
                      </Badge>
                    ) : null}
                    <span className="text-muted-foreground w-20 text-right text-xs">
                      {formatBytes(model.size_bytes, i18n.language)}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
