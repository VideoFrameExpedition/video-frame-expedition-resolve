import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { boxStyle, CATEGORY_COLORS, colorOf, subjectKey, type Subject } from "./subjectBoxes";

/**
 * Boxes around the living beings of a keyframe, drawn over its image. The parent must be
 * `relative` and exactly the size of the displayed image (boxes are 0–1 of that image).
 * Outlines (not borders) keep every box, label and face at its exact place.
 */
export function SubjectOverlay({
  subjects,
  highlighted,
}: {
  subjects: Subject[];
  highlighted?: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      {subjects.map((subject) => {
        const key = subjectKey(subject);
        const color = colorOf(subject.category);
        const width = subject.main ? 3 : 2;
        const dimmed = highlighted !== undefined && highlighted !== key;
        const [x1 = 0, y1 = 0] = subject.box;
        // Near the top edge the label goes inside the box; right of centre it is anchored on
        // the box's right side, so it never runs out of the image.
        const labelInside = y1 < 0.1;
        const labelRight = x1 > 0.5;
        return (
          <div key={key} className={cn("transition-opacity", dimmed && "opacity-25")}>
            <div
              data-testid="subject-box"
              className="absolute rounded-[3px]"
              style={{
                ...boxStyle(subject.box),
                outline: `${String(width)}px solid ${color}`,
                outlineOffset: `-${String(width)}px`,
                boxShadow: "0 0 0 1px rgb(0 0 0 / 0.55), inset 0 0 0 1px rgb(0 0 0 / 0.35)",
              }}
            >
              <span
                className={cn(
                  "absolute max-w-[16rem] truncate px-1.5 py-px text-[11px] leading-4 font-semibold text-black",
                  labelRight ? "right-0" : "left-0",
                  labelInside ? "top-0" : "top-0 -translate-y-full",
                  labelInside
                    ? labelRight
                      ? "rounded-bl-[3px]"
                      : "rounded-br-[3px]"
                    : "rounded-t-[3px]",
                )}
                style={{ backgroundColor: color }}
              >
                {subject.label}
                {subject.main ? ` · ${t("subjects.main")}` : ""}
              </span>
            </div>
            {subject.face_box && subject.category !== "face" ? (
              <div
                data-testid="face-box"
                className="absolute rounded-sm border border-dashed"
                style={{ ...boxStyle(subject.face_box), borderColor: CATEGORY_COLORS.face }}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
