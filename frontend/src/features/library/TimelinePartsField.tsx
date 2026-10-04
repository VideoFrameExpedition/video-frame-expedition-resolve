import { useId } from "react";
import { useTranslation } from "react-i18next";

import type { Schemas } from "@/api/client";
import { Label } from "@/components/ui/label";

import {
  PART_NAMES,
  SUGGESTION_KINDS,
  partCount,
  type PartName,
  type TimelineParts,
} from "./timelineParts";

type Plan = Schemas["TimelinePlanOut"];

/**
 * « With the timeline »: the four things that may go with the videos, each with what
 * it would bring; one that would bring nothing is unticked and cannot be ticked.
 */
export function TimelinePartsField({
  plan,
  parts,
  onChange,
}: {
  plan: Plan | undefined;
  parts: TimelineParts;
  onChange: (name: PartName, on: boolean) => void;
}) {
  const { t } = useTranslation();
  const base = useId();
  return (
    <fieldset className="grid gap-2.5">
      <legend className="mb-2 text-sm font-medium">{t("timelineBuild.parts.legend")}</legend>
      {PART_NAMES.map((name) => {
        const id = `${base}-${name}`;
        const count = partCount(plan, name);
        return (
          <div key={name} className="flex items-start gap-2">
            <input
              id={id}
              type="checkbox"
              checked={parts[name] && count > 0}
              disabled={count === 0}
              onChange={(event) => {
                onChange(name, event.target.checked);
              }}
              aria-describedby={`${id}-hint`}
              className="accent-primary mt-0.5 size-3.5 shrink-0"
            />
            <div className="grid min-w-0 gap-0.5">
              <Label htmlFor={id} className="leading-snug font-normal">
                {t(`timelineBuild.parts.${name}.label`)}
              </Label>
              <p id={`${id}-hint`} className="text-muted-foreground text-xs">
                {plan ? <PartHint plan={plan} name={name} count={count} /> : null}
              </p>
            </div>
          </div>
        );
      })}
    </fieldset>
  );
}

function PartHint({ plan, name, count }: { plan: Plan; name: PartName; count: number }) {
  const { t } = useTranslation();
  if (count === 0) return t(`timelineBuild.parts.${name}.none`);
  if (name === "chapters") {
    const videos = t("timelineBuild.videos", { count: plan.chaptered });
    return t("timelineBuild.parts.chapters.found", { count, videos });
  }
  if (name === "suggestions") {
    const detail = SUGGESTION_KINDS.filter((kind) => plan.suggestions[kind] > 0)
      .map((kind) =>
        t(`timelineBuild.parts.suggestions.kinds.${kind}`, { count: plan.suggestions[kind] }),
      )
      .join(", ");
    return t("timelineBuild.parts.suggestions.found", { count, detail });
  }
  return t(`timelineBuild.parts.${name}.found`, { count });
}
