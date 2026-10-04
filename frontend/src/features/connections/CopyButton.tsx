import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { copyText } from "./clipboard";

export function CopyButton({
  text,
  label,
  className,
}: {
  text: string;
  label: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => {
      setCopied(false);
    }, 2000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [copied]);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className={cn("shrink-0", className)}
      aria-label={label}
      title={label}
      onClick={() => {
        void copyText(text).then(() => {
          setCopied(true);
        });
      }}
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      {copied ? t("connections.copied") : t("connections.copy")}
    </Button>
  );
}

/** A snippet to paste, with its copy button. */
export function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="bg-muted/50 relative rounded-lg border">
      <pre className="overflow-x-auto p-3 pr-28 font-mono text-xs leading-relaxed whitespace-pre">
        {code}
      </pre>
      <CopyButton text={code} label={label} className="absolute top-2 right-2" />
    </div>
  );
}
