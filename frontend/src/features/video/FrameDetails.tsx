import { Eye, EyeOff, Maximize2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { Keyframe, Schemas } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { formatClock } from "@/lib/format";

import { CATEGORY_COLORS, subjectKey, type Subject } from "./subjectBoxes";
import { SubjectOverlay } from "./SubjectOverlay";

const BOXES_KEY = "vfe.subjectBoxes";

function storedBoxes(): boolean {
  try {
    return window.localStorage.getItem(BOXES_KEY) !== "off";
  } catch {
    return true;
  }
}

function storeBoxes(show: boolean): void {
  try {
    window.localStorage.setItem(BOXES_KEY, show ? "on" : "off");
  } catch {
    // private window or blocked storage: the choice lasts for this page only
  }
}

interface StageState {
  status: string;
  note: string | null;
}

interface SubjectsPart {
  status: string;
  run_status: string;
  note: string | null;
  detector?: StageState;
  vision?: StageState;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-1.5">
      <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{title}</h3>
      {children}
    </section>
  );
}

function Chips({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <p className="text-muted-foreground text-sm">—</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <Badge key={item} variant="secondary" className="font-normal">
          {item}
        </Badge>
      ))}
    </div>
  );
}

type OcrLine = Schemas["OcrLineOut"];

/** Chips naming the beings of the frame; hovering one highlights its box. */
function SubjectList({
  subjects,
  part,
  loading,
  error,
  onHover,
}: {
  subjects: Subject[];
  part: SubjectsPart | undefined;
  loading: boolean;
  error: string | null;
  onHover: (key: string | undefined) => void;
}) {
  const { t } = useTranslation();
  let message: string | null = null;
  if (error) {
    message = error;
  } else if (loading || !part) {
    message = "…";
  } else if (subjects.length === 0) {
    if (part.status === "running" || part.run_status === "running") {
      message = t("subjects.running");
    } else if (part.status === "not_run") {
      message = t("subjects.notRun");
    } else if (part.status === "ready") {
      message = t("subjects.none");
    } else {
      message = t("subjects.skipped", { reason: part.note ?? part.status });
    }
  }
  // One of the two sources did not look (no model, LM Studio away…): say so, or "nobody here"
  // would read as a full answer.
  const partial =
    part?.status === "ready"
      ? (["detector", "vision"] as const).flatMap((source) => {
          const state = part[source];
          if (!state || state.status === "ready" || state.status === "running") {
            return [];
          }
          return [
            t("subjects.partial", {
              source: t(`subjects.source.${source}`),
              reason: state.note ?? t(`subjects.state.${state.status}`, state.status),
            }),
          ];
        })
      : [];
  return (
    <Section title={t("subjects.title")}>
      {message ? (
        <p className="text-muted-foreground text-sm">{message}</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {subjects.map((subject) => (
            <li
              key={subjectKey(subject)}
              className="bg-secondary flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs"
              title={t(`subjects.category.${subject.category}`, {
                defaultValue: subject.category,
              })}
              onMouseEnter={() => {
                onHover(subjectKey(subject));
              }}
              onMouseLeave={() => {
                onHover(undefined);
              }}
            >
              <span
                className="size-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: CATEGORY_COLORS[subject.category] ?? "#fff" }}
                aria-hidden
              />
              <span className="font-medium">{subject.label}</span>
              {subject.main ? (
                <span className="text-brand-teal">({t("subjects.main")})</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {partial.map((line) => (
        <p key={line} className="text-muted-foreground text-xs">
          {line}
        </p>
      ))}
      <p className="text-muted-foreground text-xs">{t("subjects.privacy")}</p>
    </Section>
  );
}

export function FrameDetails({
  frame,
  ocr = [],
  subjects = [],
  subjectsPart,
  subjectsLoading = false,
  subjectsError = null,
}: {
  frame: Keyframe | undefined;
  ocr?: OcrLine[];
  subjects?: Subject[];
  subjectsPart?: SubjectsPart;
  subjectsLoading?: boolean;
  subjectsError?: string | null;
}) {
  const { t } = useTranslation();
  const [showBoxes, setShowBoxes] = useState(storedBoxes);
  // Reset with each frame: the page gives this component a key per keyframe.
  const [hovered, setHovered] = useState<string>();
  if (!frame) {
    return (
      <Card>
        <CardContent className="text-muted-foreground py-10 text-center text-sm">
          {t("video.selectFrame")}
        </CardContent>
      </Card>
    );
  }
  const data = frame.analysis?.data;
  const subjectsSection = (
    <SubjectList
      subjects={subjects}
      part={subjectsPart}
      loading={subjectsLoading}
      error={subjectsError}
      onHover={setHovered}
    />
  );
  // Text read by OCR does not depend on the vision model: shown even before a description.
  const ocrSection = ocr.length ? (
    <Section title={t("video.ocrText")}>
      <ul className="bg-muted grid gap-0.5 rounded-md px-3 py-2 font-mono text-xs">
        {ocr.map((line) => (
          <li key={line.idx} title={`${Math.round(line.score * 100)} %`}>
            {line.text}
          </li>
        ))}
      </ul>
    </Section>
  ) : null;
  return (
    <Card className="overflow-hidden pt-0">
      <div className="flex justify-center bg-black">
        <div className="relative w-fit max-w-full">
          <img
            src={frame.image_url}
            alt={data?.caption ?? ""}
            className="block max-h-[50vh] max-w-full"
          />
          {showBoxes && subjects.length > 0 ? (
            <SubjectOverlay subjects={subjects} highlighted={hovered} />
          ) : null}
          <div className="absolute right-2 bottom-2 flex gap-1.5">
            {subjects.length > 0 ? (
              <Button
                type="button"
                size="icon"
                variant="secondary"
                className="size-8 opacity-80 hover:opacity-100"
                aria-pressed={showBoxes}
                aria-label={t("subjects.boxes")}
                title={showBoxes ? t("subjects.hide") : t("subjects.show")}
                onClick={() => {
                  storeBoxes(!showBoxes);
                  setShowBoxes(!showBoxes);
                }}
              >
                {showBoxes ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </Button>
            ) : null}
            <Dialog>
              <DialogTrigger asChild>
                <Button
                  type="button"
                  size="icon"
                  variant="secondary"
                  className="size-8 opacity-80 hover:opacity-100"
                  aria-label={t("subjects.enlarge")}
                  title={t("subjects.enlarge")}
                >
                  <Maximize2 className="size-4" />
                </Button>
              </DialogTrigger>
              <DialogContent className="w-auto max-w-[96vw] border-0 bg-black p-0 sm:max-w-[96vw]">
                <DialogTitle className="sr-only">
                  {t("video.frame", { index: frame.idx + 1, time: formatClock(frame.t_s, true) })}
                </DialogTitle>
                <div className="relative mx-auto w-fit">
                  <img
                    src={frame.image_url}
                    alt={data?.caption ?? ""}
                    className="block max-h-[90vh] max-w-[96vw]"
                  />
                  {showBoxes && subjects.length > 0 ? (
                    <SubjectOverlay subjects={subjects} highlighted={hovered} />
                  ) : null}
                </div>
              </DialogContent>
            </Dialog>
          </div>
        </div>
      </div>
      <CardHeader className="gap-1">
        <p className="text-muted-foreground font-mono text-xs">
          {t("video.frame", { index: frame.idx + 1, time: formatClock(frame.t_s, true) })}
          {" · "}
          {t(`video.reason.${frame.selection_reason}`, { defaultValue: frame.selection_reason })}
        </p>
        <CardTitle className="text-base leading-snug">
          {data?.caption ?? t("video.notDescribed")}
        </CardTitle>
      </CardHeader>
      {data ? (
        <CardContent className="grid gap-4">
          <p className="text-sm leading-relaxed">{data.description}</p>
          <div className="flex flex-wrap gap-1.5">
            <Badge className="bg-brand-azure/15 text-brand-azure border-0">
              {t(`analysis.shot_type.${data.shot_type}`)}
            </Badge>
            <Badge variant="secondary">{t(`analysis.camera_angle.${data.camera_angle}`)}</Badge>
            <Badge variant="secondary">{t(`analysis.setting.${data.setting}`)}</Badge>
            <Badge variant="secondary">{t(`analysis.time_of_day.${data.time_of_day}`)}</Badge>
            <Badge variant="secondary">{t(`analysis.weather.${data.weather}`)}</Badge>
            <Badge variant="secondary">{t(`analysis.lighting.${data.lighting}`)}</Badge>
          </div>
          {subjectsSection}
          <Separator />
          <Section title={t("video.place")}>
            <p className="text-sm">{data.place_type}</p>
          </Section>
          <Section title={t("video.subjects")}>
            <ul className="grid gap-1 text-sm">
              {data.subjects.map((subject) => (
                <li key={`${subject.label}-${subject.description}`}>
                  <span className="font-medium">{subject.label}</span>
                  {subject.is_main ? (
                    <span className="text-brand-teal ml-1 text-xs">({t("video.main")})</span>
                  ) : null}
                  <span className="text-muted-foreground"> — {subject.description}</span>
                </li>
              ))}
            </ul>
          </Section>
          {data.actions.length > 0 ? (
            <Section title={t("video.actions")}>
              <Chips items={data.actions} />
            </Section>
          ) : null}
          <Section title={t("video.mood")}>
            <p className="text-sm">{data.mood}</p>
          </Section>
          <Section title={t("video.colors")}>
            <Chips items={data.dominant_colors} />
          </Section>
          {ocrSection}
          {data.visible_text ? (
            <Section title={t("video.visibleText")}>
              <blockquote className="bg-muted rounded-md px-3 py-2 font-mono text-xs whitespace-pre-wrap">
                {data.visible_text}
              </blockquote>
            </Section>
          ) : null}
          {data.editing_value.length > 0 ? (
            <Section title={t("video.editing")}>
              <Chips items={data.editing_value.map((v) => t(`analysis.editing.${v}`))} />
            </Section>
          ) : null}
          {data.quality_issues.length > 0 ? (
            <Section title={t("video.quality")}>
              <Chips items={data.quality_issues.map((v) => t(`analysis.quality.${v}`))} />
            </Section>
          ) : null}
          <Section title={t("video.tags")}>
            <Chips items={data.tags} />
          </Section>
          {frame.analysis ? (
            <p className="text-muted-foreground text-xs">
              {t("video.model", { model: frame.analysis.model })}
            </p>
          ) : null}
        </CardContent>
      ) : (
        <CardContent className="grid gap-4">
          {subjectsSection}
          {ocrSection}
        </CardContent>
      )}
    </Card>
  );
}
