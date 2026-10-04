import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "@/api/client";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

type Orientation = Schemas["Orientation"];

export function OrientationBadge({
  orientation,
  className,
}: {
  orientation: Orientation | null | undefined;
  className?: string;
}) {
  const { t } = useTranslation();
  if (!orientation) {
    return null;
  }
  const colors: Record<Orientation, string> = {
    horizontal: "bg-brand-azure/85",
    vertical: "bg-brand-teal/85 text-black",
    square: "bg-warning/85 text-black",
  };
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex size-5 items-center justify-center rounded text-[0.65rem] font-bold text-white",
            colors[orientation],
            className,
          )}
          aria-label={t(`orientation.${orientation}`)}
        >
          {t(`orientation.short.${orientation}`)}
        </span>
      </TooltipTrigger>
      <TooltipContent>{t(`orientation.${orientation}`)}</TooltipContent>
    </Tooltip>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="truncate text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? <p className="text-muted-foreground mt-1 text-sm">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon?: ReactNode;
  title: string;
  body?: string;
  action?: ReactNode;
}) {
  return (
    <div className="glass flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-14 text-center">
      {icon ? <div className="text-muted-foreground">{icon}</div> : null}
      <h2 className="text-lg font-semibold">{title}</h2>
      {body ? <p className="text-muted-foreground max-w-md text-sm">{body}</p> : null}
      {action}
    </div>
  );
}

export function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-sm">
      <dt className="text-muted-foreground shrink-0">{label}</dt>
      <dd className="min-w-0 text-right break-words">{value}</dd>
    </div>
  );
}
