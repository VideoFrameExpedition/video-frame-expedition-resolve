import { useTranslation } from "react-i18next";

import compactDark from "@/assets/brand/lockup-compact-dark.svg";
import compactLight from "@/assets/brand/lockup-compact-light.svg";
import lockupDark from "@/assets/brand/lockup-dark.svg";
import lockupLight from "@/assets/brand/lockup-light.svg";
import { cn } from "@/lib/utils";

const FILES = {
  // The symbol and the three lines of the name: sidebar, login page.
  full: { light: lockupLight, dark: lockupDark },
  // The symbol and « Video Frame Expedition » only, for the mobile header, where « for DaVinci
  // Resolve » would be too small to read.
  compact: { light: compactLight, dark: compactDark },
} as const;

/**
 * The logo (docs/brand/README.md): the symbol « Le Repère » and the name, in the colours of the
 * current theme. The SVG files are drawn by `docs/brand/build.cjs`; their text is outlines, so no
 * font is needed. Size it by its height (`h-14`): the width follows.
 */
export function Logo({
  variant = "full",
  className,
}: {
  variant?: keyof typeof FILES;
  className?: string;
}) {
  const { t } = useTranslation();
  const files = FILES[variant];
  return (
    <span role="img" aria-label={t("app.fullName")} className={cn("block w-fit", className)}>
      <img src={files.light} alt="" className="h-full w-auto dark:hidden" />
      <img src={files.dark} alt="" className="hidden h-full w-auto dark:block" />
    </span>
  );
}
