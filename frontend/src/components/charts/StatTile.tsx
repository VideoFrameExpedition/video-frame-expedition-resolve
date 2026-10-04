import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** A headline number: label, value, optional note, optional warning (icon + text, never colour alone). */
export function StatTile({
  label,
  value,
  note,
  warning,
  className,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  warning?: string | undefined;
  className?: string;
}) {
  return (
    <div
      className={cn("bg-card grid content-start gap-0.5 rounded-lg border px-3.5 py-3", className)}
    >
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="text-xl font-semibold tracking-tight">{value}</p>
      {note ? <p className="text-muted-foreground text-xs">{note}</p> : null}
      {warning ? (
        <p className="text-warning flex items-center gap-1 text-xs font-medium">
          <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
          {warning}
        </p>
      ) : null}
    </div>
  );
}
