import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { paramsChip, quantChip, useFamilyColor, type Chip } from "./modelCodes";

/** The colour of a model's family, beside its name. */
export function FamilyDot({ family, className }: { family: string; className?: string }) {
  const color = useFamilyColor(family);
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block size-2.5 shrink-0 rounded-full ring-1 ring-black/15 dark:ring-white/25",
        className,
      )}
      style={{ backgroundColor: color }}
    />
  );
}

function ChipText({ chip, children }: { chip: Chip | null; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex w-fit shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-black/10 ring-inset dark:ring-white/15",
        !chip && "bg-secondary text-secondary-foreground",
      )}
      style={chip ? { backgroundColor: chip.background, color: chip.ink } : undefined}
    >
      {children}
    </span>
  );
}

/** The number of parameters, coloured by its step. */
export function ParamsChip({ params }: { params: string | null | undefined }) {
  return params ? <ChipText chip={paramsChip(params)}>{params}</ChipText> : null;
}

/** The quantization, coloured by its bits per weight. */
export function QuantChip({ quantization }: { quantization: string | null | undefined }) {
  return quantization ? <ChipText chip={quantChip(quantization)}>{quantization}</ChipText> : null;
}
