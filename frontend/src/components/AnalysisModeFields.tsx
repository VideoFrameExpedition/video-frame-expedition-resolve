import { useId } from "react";
import { useTranslation } from "react-i18next";

import type { AnalysisMode } from "@/api/client";

const MODES = ["complete", "update", "full"] as const satisfies AnalysisMode[];

/** How much of an existing analysis to redo: finished work is kept by default.
 * ``chosen``: the request only concerns the stages the user ticked. */
export function AnalysisModeFields({
  value,
  onChange,
  scope = "all",
}: {
  value: AnalysisMode;
  onChange: (mode: AnalysisMode) => void;
  scope?: "all" | "chosen";
}) {
  const { t } = useTranslation();
  const name = useId();
  const keys =
    scope === "chosen"
      ? { mode: "analysisMode.modeChosen", hint: "analysisMode.hintChosen" }
      : { mode: "analysisMode.mode", hint: "analysisMode.hint" };
  return (
    <fieldset className="grid gap-2">
      <legend className="mb-2 text-sm font-medium">{t("analysisMode.legend")}</legend>
      {MODES.map((mode) => (
        <label
          key={mode}
          className="has-checked:border-primary has-checked:bg-primary/5 has-focus-visible:ring-ring/50 grid cursor-pointer grid-cols-[auto_1fr] items-start gap-x-3 gap-y-0.5 rounded-lg border p-3 has-focus-visible:ring-[3px]"
        >
          <input
            type="radio"
            name={name}
            value={mode}
            checked={value === mode}
            onChange={() => {
              onChange(mode);
            }}
            className="accent-primary row-span-2 mt-0.5 size-4 outline-none"
          />
          <span className="text-sm font-medium">{t(`${keys.mode}.${mode}`)}</span>
          <span className="text-muted-foreground col-start-2 text-xs">
            {t(`${keys.hint}.${mode}`)}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
