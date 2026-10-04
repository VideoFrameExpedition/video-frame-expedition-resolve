import { useId, useRef, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * A badge that explains itself: hovering, focusing or tapping it shows ``hint``, and screen
 * readers get the hint as its description (a title alone reaches none of them).
 */
export function HintBadge({
  hint,
  className,
  variant,
  children,
}: {
  hint: ReactNode;
  className?: string;
  variant?: "default" | "secondary" | "outline";
  children: ReactNode;
}) {
  const hintId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return (
    <>
      <Tooltip open={open} onOpenChange={setOpen}>
        <TooltipTrigger asChild>
          <Badge asChild variant={variant} className={cn("cursor-help align-middle", className)}>
            <button
              ref={trigger}
              type="button"
              aria-describedby={hintId}
              onPointerDown={(event) => {
                // The trigger closes the tooltip on press: the click below would reopen it.
                event.preventDefault();
              }}
              onClick={(event) => {
                // A tap has no hover: it opens the hint, which the tooltip would close on click.
                event.preventDefault();
                setOpen(true);
              }}
            >
              {children}
            </button>
          </Badge>
        </TooltipTrigger>
        <TooltipContent
          className="max-w-xs"
          onPointerDownOutside={(event) => {
            // So does the content, for a press on its own trigger (a flicker on each click).
            if (event.target instanceof Node && trigger.current?.contains(event.target)) {
              event.preventDefault();
            }
          }}
        >
          {hint}
        </TooltipContent>
      </Tooltip>
      {/* The description only: hidden, so it is not read a second time after the button. */}
      <span id={hintId} hidden>
        {hint}
      </span>
    </>
  );
}
