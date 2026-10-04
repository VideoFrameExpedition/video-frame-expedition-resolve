import { History, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AskSummary } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The questions asked before: reopen one, or delete it. */
export function QuestionHistory({
  items,
  current,
  onOpen,
  onDelete,
  deleting,
}: {
  items: AskSummary[] | undefined;
  current: string | undefined;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  deleting: boolean;
}) {
  const { t, i18n } = useTranslation();
  return (
    <section className="grid content-start gap-2" aria-labelledby="ask-history">
      <h2
        id="ask-history"
        className="text-muted-foreground flex items-center gap-2 text-xs font-medium tracking-wide uppercase"
      >
        <History className="size-3.5" aria-hidden />
        {t("ask.history.title")}
      </h2>
      {items === undefined ? (
        <Skeleton className="h-24 rounded-lg" />
      ) : items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("ask.history.empty")}</p>
      ) : (
        <ul className="grid gap-1">
          {items.map((item) => (
            <li key={item.id} className="group flex items-start gap-1">
              <button
                type="button"
                onClick={() => {
                  onOpen(item.id);
                }}
                aria-current={item.id === current ? "true" : undefined}
                aria-label={t("ask.history.open", { question: item.question })}
                className={cn(
                  "hover:bg-secondary/70 focus-visible:ring-ring min-w-0 flex-1 rounded-lg px-2 py-1.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none",
                  item.id === current && "bg-secondary",
                )}
              >
                <span className="line-clamp-2 text-sm break-words">{item.question}</span>
                <span className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
                  {formatDateTime(item.created_at, i18n.language)}
                  {item.status === "answered" ? (
                    <span>· {t("ask.history.sources", { count: item.citations })}</span>
                  ) : (
                    <Badge variant="secondary" className="h-4 px-1.5 text-[0.65rem]">
                      {t(`ask.history.status.${item.status}`)}
                    </Badge>
                  )}
                </span>
              </button>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 shrink-0 opacity-70 group-hover:opacity-100 focus-visible:opacity-100"
                disabled={deleting}
                aria-label={t("ask.history.delete", { question: item.question })}
                onClick={() => {
                  onDelete(item.id);
                }}
              >
                <Trash2 className="size-4" aria-hidden />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
