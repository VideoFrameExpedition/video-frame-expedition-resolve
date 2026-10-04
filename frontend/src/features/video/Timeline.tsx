import { useMemo, useState, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";

import type {
  AudioCurve,
  Keyframe,
  Shot,
  SynthesisChapter,
  SynthesisHighlight,
} from "@/api/client";
import {
  areaPath,
  linearScale,
  linePath,
  nearestIndex,
  spanIndexAt,
  timeTicks,
  type LinearScale,
} from "@/components/charts/scale";
import { useElementWidth } from "@/components/charts/useElementWidth";
import { formatClock, formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import { FAMILY_FILL, MOTION_FAMILIES, motionFamily, motionGlyph } from "./motion";
import { usePlayer, usePlayheadSelector, usePlayheadTime } from "./player";
import { storyAt } from "./stories";
import { soundRange } from "./synthesis/labels";

const LUFS_FLOOR = -60;
const KEYFRAME_HIT_PX = 8;
/** A highlight's hover and click area reaches this far past its marks (they can be 2 px wide). */
const HIGHLIGHT_HIT_PX = 6;
const LABEL_INK = "var(--chart-ink)";
const NO_CHAPTERS: SynthesisChapter[] = [];
const NO_HIGHLIGHTS: SynthesisHighlight[] = [];

type TrackName = "chapters" | "shots" | "frames" | "highlights" | "audio";

interface Band {
  top: number;
  height: number;
}

/** Vertical layout of the tracks inside the SVG (pixels); the synthesis tracks only with data. */
interface Layout {
  chapters: Band | null;
  shots: Band;
  frames: Band;
  highlights: Band | null;
  audio: Band;
  axisTop: number;
  height: number;
}

function trackLayout(withChapters: boolean, withHighlights: boolean): Layout {
  let top = 0;
  const chapters = withChapters ? { top, height: 20 } : null;
  top += chapters ? 26 : 0;
  const shots = { top, height: 28 };
  top += 34;
  const frames = { top, height: 16 };
  top += 22;
  const highlights = withHighlights ? { top, height: 14 } : null;
  top += highlights ? 20 : 0;
  const audio = { top, height: 44 };
  top += 50;
  return { chapters, shots, frames, highlights, audio, axisTop: top, height: top + 18 };
}

function trackAt(layout: Layout, y: number): TrackName | null {
  const within = (band: Band | null, slack = 0): boolean =>
    band !== null && y >= band.top - slack && y <= band.top + band.height + slack;
  if (within(layout.chapters)) return "chapters";
  if (within(layout.shots)) return "shots";
  if (within(layout.frames, 3)) return "frames";
  if (within(layout.highlights, 3)) return "highlights";
  if (within(layout.audio)) return "audio";
  return null;
}

function insideSilence(silences: readonly number[][], t: number): boolean {
  return silences.some(([start = 0, end = 0]) => t >= start && t <= end);
}

/** Everything a highlight covers: its picture, and the sound of a J-cut or an L-cut. */
function highlightSpan(highlight: SynthesisHighlight): [number, number] {
  const { picture_in_s: start, picture_out_s: end } = highlight.clip;
  const sound = soundRange(highlight.clip);
  return sound ? [Math.min(start, sound[0]), Math.max(end, sound[1])] : [start, end];
}

/**
 * Horizontal shift of the tooltip placed at ``x`` on a track ``width`` px wide: centred on the
 * pointer, slid back by its real width near an edge (percentages of a translate are the
 * tooltip's own size), so a long story never leaves the track.
 */
function tooltipShift(x: number, width: number): string {
  return `max(${-x}px, min(-50%, ${width - x}px - 100%))`;
}

/**
 * A chapter's label for a segment ``px`` pixels wide (about 6.2 px a character): its number and
 * title, the title cut with « … » when it keeps at least 3 letters, else the number alone.
 */
function chapterLabel(chapter: SynthesisChapter, px: number): string | null {
  const room = Math.floor(px / 6.2);
  const number = String(chapter.index);
  const full = `${number} ${chapter.title}`;
  if (full.length <= room) return full;
  const title = chapter.title.slice(0, Math.max(0, room - number.length - 2)).trimEnd();
  if (title.length >= 3) return `${number} ${title}…`;
  return number.length * 7 <= px ? number : null;
}

export interface TimelineProps {
  duration: number;
  shots: Shot[];
  keyframes: Keyframe[];
  audio: AudioCurve | null;
  silences: number[][];
  hasAudio: boolean;
  selectedKeyframeId: string | undefined;
  onSelectKeyframe: (frame: Keyframe) => void;
  /** From the synthesis: a band of chapters, drawn when there are some. */
  chapters?: SynthesisChapter[];
  /** From the synthesis: suggested highlights (picture, and the sound of a J/L-cut). */
  highlights?: SynthesisHighlight[];
}

interface Hover {
  t: number;
  x: number;
  track: TrackName | null;
}

export function Timeline(props: TimelineProps) {
  const { duration, shots, keyframes, audio, silences, hasAudio } = props;
  const chapters = props.chapters ?? NO_CHAPTERS;
  const highlights = props.highlights ?? NO_HIGHLIGHTS;
  const { t, i18n } = useTranslation();
  const { seek, playhead } = usePlayer();
  const [measureRef, width] = useElementWidth(720);
  const [hover, setHover] = useState<Hover | null>(null);
  const layout = useMemo(
    () => trackLayout(chapters.length > 0, highlights.length > 0),
    [chapters.length, highlights.length],
  );
  const { axisTop, height } = layout;

  const x = useMemo(
    () => linearScale([0, Math.max(duration, 0.001)], [0, width]),
    [duration, width],
  );
  const lufsY = useMemo(
    () => linearScale([LUFS_FLOOR, 0], [layout.audio.top + layout.audio.height, layout.audio.top]),
    [layout],
  );
  const shotStarts = useMemo(() => shots.map((shot) => shot.start_s), [shots]);
  const chapterStarts = useMemo(() => chapters.map((chapter) => chapter.start_s), [chapters]);
  const frameTimes = useMemo(() => keyframes.map((frame) => frame.t_s), [keyframes]);
  const loudness = useMemo(
    () => (audio ? audio.lufs.map((v) => Math.max(LUFS_FLOOR, Math.min(0, v))) : []),
    [audio],
  );
  const families = useMemo(
    () => MOTION_FAMILIES.filter((family) => shots.some((s) => motionFamily(s.motion) === family)),
    [shots],
  );
  const withSoundCuts = useMemo(
    () => highlights.some((highlight) => soundRange(highlight.clip) !== null),
    [highlights],
  );
  const currentSecond = usePlayheadSelector((time) => Math.floor(time));

  const clampTime = (time: number): number => Math.max(0, Math.min(duration, time));

  const nearestFrame = (px: number): Keyframe | undefined => {
    const index = nearestIndex(frameTimes, x.invert(px));
    const frame = keyframes[index];
    return frame && Math.abs(x(frame.t_s) - px) <= KEYFRAME_HIT_PX ? frame : undefined;
  };

  const chapterAt = (time: number): SynthesisChapter | undefined =>
    chapters[Math.max(0, spanIndexAt(chapterStarts, time))];

  /** The highlight under ``px`` (its marks and a margin), the nearest picture when several. */
  const highlightAt = (px: number): SynthesisHighlight | undefined => {
    let best: SynthesisHighlight | undefined;
    let bestDistance = Infinity;
    for (const highlight of highlights) {
      const [start, end] = highlightSpan(highlight);
      if (px < x(start) - HIGHLIGHT_HIT_PX || px > x(end) + HIGHLIGHT_HIT_PX) continue;
      const { picture_in_s: pictureIn, picture_out_s: pictureOut } = highlight.clip;
      const distance = Math.max(0, x(pictureIn) - px, px - x(pictureOut));
      if (distance < bestDistance) {
        best = highlight;
        bestDistance = distance;
      }
    }
    return best;
  };

  const onPointerMove = (event: PointerEvent<SVGSVGElement>): void => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = Math.max(0, Math.min(width, event.clientX - rect.left));
    setHover({
      t: clampTime(x.invert(px)),
      x: px,
      track: trackAt(layout, event.clientY - rect.top),
    });
  };

  const onClick = (event: PointerEvent<SVGSVGElement>): void => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const time = clampTime(x.invert(px));
    const track = trackAt(layout, event.clientY - rect.top);
    const frame = track === "frames" ? nearestFrame(px) : undefined;
    const chapter = track === "chapters" ? chapterAt(time) : undefined;
    const highlight = track === "highlights" ? highlightAt(px) : undefined;
    if (frame) {
      props.onSelectKeyframe(frame);
    } else if (chapter) {
      seek(chapter.start_s);
    } else if (highlight) {
      seek(highlight.clip.picture_in_s);
    } else {
      seek(time);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const now = playhead.get();
    const step = event.shiftKey ? 10 : 1;
    const shotIndex = spanIndexAt(shotStarts, now);
    const targets: Record<string, number | undefined> = {
      ArrowLeft: now - step,
      ArrowRight: now + step,
      Home: 0,
      End: duration,
      PageUp: shotStarts[now - (shotStarts[shotIndex] ?? 0) > 0.5 ? shotIndex : shotIndex - 1],
      PageDown: shotStarts[shotIndex + 1],
    };
    if (event.key in targets) {
      event.preventDefault();
      const target = targets[event.key];
      if (target !== undefined) {
        seek(clampTime(target));
      }
    }
  };

  const hoverShot = hover ? shots[spanIndexAt(shotStarts, hover.t)] : undefined;
  const hoverFrame = hover?.track === "frames" ? nearestFrame(hover.x) : undefined;
  const hoverChapter = hover?.track === "chapters" ? chapterAt(hover.t) : undefined;
  const hoverHighlight = hover?.track === "highlights" ? highlightAt(hover.x) : undefined;
  // Over the shots track, what happens in the shot (the part under the pointer for a long one).
  const hoverStory =
    hover?.track === "shots" && hoverShot ? storyAt(hoverShot.stories, hover.t) : undefined;
  const hoverLufs = hover && audio ? audio.lufs[nearestIndex(audio.t, hover.t)] : undefined;
  const hoverSound = hoverHighlight ? soundRange(hoverHighlight.clip) : null;
  const ticks = timeTicks(duration, width);
  const range = (start: number, end: number): string =>
    t("synthesis.range", { start: formatClock(start, true), end: formatClock(end, true) });

  return (
    <section className="grid gap-2" aria-labelledby="timeline-title">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 id="timeline-title" className="text-lg font-semibold">
          {t("timeline.title")}
        </h2>
        <ul className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {families.map((family) => (
            <li key={family} className="flex items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-3.5 rounded-sm"
                style={{ background: FAMILY_FILL[family] }}
                aria-hidden
              />
              {t(`motion.family.${family}`)}
            </li>
          ))}
          {silences.length > 0 ? (
            <li className="flex items-center gap-1.5">
              <span
                className="bg-chart-neutral/25 inline-block h-2.5 w-3.5 rounded-sm"
                aria-hidden
              />
              {t("timeline.silence")}
            </li>
          ) : null}
          {highlights.length > 0 ? (
            <li className="flex items-center gap-1.5">
              <span className="bg-foreground/75 inline-block h-2.5 w-3.5 rounded-sm" aria-hidden />
              {t("timeline.legend.highlight")}
            </li>
          ) : null}
          {withSoundCuts ? (
            <li className="flex items-center gap-1.5">
              <span className="bg-foreground/45 inline-block h-1 w-3.5 rounded-sm" aria-hidden />
              {t("timeline.legend.sound")}
            </li>
          ) : null}
        </ul>
      </div>
      <div
        className={cn(
          "grid gap-x-3",
          highlights.length > 0
            ? "grid-cols-[5.5rem_minmax(0,1fr)]"
            : "grid-cols-[4.5rem_minmax(0,1fr)]",
        )}
      >
        <div
          className="text-muted-foreground relative text-xs whitespace-nowrap"
          style={{ height }}
        >
          {layout.chapters ? (
            <span className="absolute" style={{ top: layout.chapters.top + 2 }}>
              {t("timeline.tracks.chapters")}
            </span>
          ) : null}
          <span className="absolute" style={{ top: layout.shots.top + 7 }}>
            {t("timeline.tracks.shots")}
          </span>
          <span className="absolute" style={{ top: layout.frames.top }}>
            {t("timeline.tracks.keyframes")}
          </span>
          {layout.highlights ? (
            <span className="absolute" style={{ top: layout.highlights.top - 1 }}>
              {t("timeline.tracks.highlights")}
            </span>
          ) : null}
          <span className="absolute" style={{ top: layout.audio.top + 14 }}>
            {t("timeline.tracks.audio")}
          </span>
        </div>
        <div
          ref={measureRef}
          className="focus-visible:ring-ring relative rounded-sm outline-none focus-visible:ring-2"
          role="slider"
          tabIndex={0}
          aria-label={t("timeline.aria")}
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={currentSecond}
          aria-valuetext={formatClock(currentSecond)}
          aria-keyshortcuts="ArrowLeft ArrowRight Home End PageUp PageDown"
          onKeyDown={onKeyDown}
        >
          <svg
            width={width}
            height={height}
            className="block cursor-pointer touch-none select-none"
            onPointerMove={onPointerMove}
            onPointerLeave={() => {
              setHover(null);
            }}
            onClick={onClick}
            aria-hidden
          >
            {ticks.map((tick) => (
              <line
                key={tick}
                x1={x(tick)}
                x2={x(tick)}
                y1={0}
                y2={axisTop - 2}
                stroke="var(--chart-grid)"
                strokeWidth={1}
              />
            ))}

            {layout.chapters ? (
              <ChapterBand
                chapters={chapters}
                x={x}
                band={layout.chapters}
                hovered={hoverChapter?.index}
              />
            ) : null}

            {shots.length > 0 ? (
              <ShotSegments shots={shots} x={x} band={layout.shots} />
            ) : (
              <TrackPlaceholder band={layout.shots} width={width}>
                {t("timeline.notComputed")}
              </TrackPlaceholder>
            )}

            {keyframes.map((frame) => {
              const selected = frame.id === props.selectedKeyframeId;
              return (
                <rect
                  key={frame.id}
                  x={x(frame.t_s) - (selected ? 1.5 : 1)}
                  y={layout.frames.top}
                  width={selected ? 3 : 2}
                  height={layout.frames.height}
                  rx={1}
                  fill={selected ? "var(--primary)" : "var(--foreground)"}
                  opacity={selected ? 1 : frame.analysis ? 0.75 : 0.35}
                />
              );
            })}

            {layout.highlights ? (
              <HighlightMarks
                highlights={highlights}
                x={x}
                band={layout.highlights}
                hovered={hoverHighlight?.rank}
              />
            ) : null}

            {audio && loudness.length > 1 ? (
              <g>
                {silences.map(([start = 0, end = 0]) => (
                  <rect
                    key={`${start}-${end}`}
                    x={x(start)}
                    y={layout.audio.top}
                    width={Math.max(1, x(end) - x(start))}
                    height={layout.audio.height}
                    fill="var(--chart-neutral)"
                    opacity={0.25}
                  />
                ))}
                <path
                  d={areaPath(audio.t, loudness, x, lufsY, layout.audio.top + layout.audio.height)}
                  fill="var(--muted-foreground)"
                  opacity={0.12}
                />
                <path
                  d={linePath(audio.t, loudness, x, lufsY)}
                  fill="none"
                  stroke="var(--muted-foreground)"
                  strokeWidth={1.5}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </g>
            ) : (
              <TrackPlaceholder band={layout.audio} width={width}>
                {hasAudio ? t("timeline.notComputed") : t("timeline.noAudio")}
              </TrackPlaceholder>
            )}

            <line
              x1={0}
              x2={width}
              y1={axisTop}
              y2={axisTop}
              stroke="var(--chart-axis)"
              strokeWidth={1}
            />
            {ticks.map((tick, index) => (
              <text
                key={tick}
                x={x(tick)}
                y={axisTop + 13}
                fontSize={10}
                fill="var(--muted-foreground)"
                textAnchor={index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle"}
                className="tabular-nums"
              >
                {formatClock(tick)}
              </text>
            ))}

            {hover ? (
              <line
                x1={hover.x}
                x2={hover.x}
                y1={0}
                y2={axisTop}
                stroke="var(--foreground)"
                strokeOpacity={0.35}
                strokeWidth={1}
              />
            ) : null}
            <PlayheadMarker x={x} axisTop={axisTop} />
          </svg>

          {hover ? (
            <div
              className="bg-popover text-popover-foreground pointer-events-none absolute bottom-full z-10 mb-2 w-max max-w-[min(20rem,100%)] rounded-md border px-2.5 py-1.5 text-xs shadow-md"
              style={{ left: hover.x, translate: tooltipShift(hover.x, width) }}
              role="presentation"
            >
              <p className="font-mono font-semibold tabular-nums">{formatClock(hover.t, true)}</p>
              {hoverChapter ? (
                // Over a synthesis track, only what it shows: the tooltip stays short.
                <>
                  <p className="font-medium">
                    {t("timeline.chapter", {
                      index: hoverChapter.index,
                      title: hoverChapter.title,
                    })}
                  </p>
                  <p className="text-muted-foreground font-mono tabular-nums">
                    {range(hoverChapter.start_s, hoverChapter.end_s)}
                  </p>
                  <p className="text-pretty">{hoverChapter.summary}</p>
                </>
              ) : hoverHighlight ? (
                <>
                  <p className="font-medium">
                    {t("timeline.highlight", { rank: hoverHighlight.rank })}
                  </p>
                  <p className="text-muted-foreground flex items-center gap-1.5">
                    <span className="bg-foreground/75 inline-block h-1.5 w-3 rounded" aria-hidden />
                    <span className="font-mono tabular-nums">
                      {t("synthesis.picture")}{" "}
                      {range(hoverHighlight.clip.picture_in_s, hoverHighlight.clip.picture_out_s)}
                    </span>
                  </p>
                  {hoverSound ? (
                    <p className="text-muted-foreground flex items-center gap-1.5">
                      <span
                        className="bg-foreground/45 inline-block h-0.5 w-3 rounded"
                        aria-hidden
                      />
                      <span className="font-mono tabular-nums">
                        {t("synthesis.sound")} {range(hoverSound[0], hoverSound[1])}
                      </span>
                    </p>
                  ) : null}
                  {hoverHighlight.reason ? (
                    <p className="text-pretty">{hoverHighlight.reason}</p>
                  ) : null}
                </>
              ) : (
                <>
                  {hoverFrame ? (
                    <p className="text-muted-foreground">
                      {t("timeline.keyframe", { index: hoverFrame.idx + 1 })}
                      {" · "}
                      {hoverFrame.analysis?.data.caption ?? t("video.notDescribed")}
                    </p>
                  ) : null}
                  {hoverShot ? (
                    <p className="text-muted-foreground flex items-center gap-1.5">
                      <span
                        className="inline-block h-0.5 w-3 rounded"
                        style={{ background: FAMILY_FILL[motionFamily(hoverShot.motion)] }}
                        aria-hidden
                      />
                      <span>
                        {t("timeline.shot", { index: hoverShot.idx + 1 })} ·{" "}
                        {motionGlyph(hoverShot.motion)} {t(`motion.${hoverShot.motion}`)} ·{" "}
                        {t("timeline.stability", { value: formatPercent(hoverShot.stability) })}
                      </span>
                    </p>
                  ) : null}
                  {hoverStory ? <p className="text-pretty">{hoverStory.summary}</p> : null}
                  {audio ? (
                    <p className="text-muted-foreground">
                      {insideSilence(silences, hover.t)
                        ? t("timeline.silence")
                        : formatNumber(hoverLufs, i18n.language, 0, "LUFS")}
                    </p>
                  ) : null}
                </>
              )}
            </div>
          ) : null}
        </div>
      </div>
      <p className="text-muted-foreground text-xs">{t("timeline.help")}</p>
    </section>
  );
}

function ShotSegments({ shots, x, band }: { shots: Shot[]; x: LinearScale; band: Band }) {
  return (
    <g>
      {shots.map((shot) => {
        const left = x(shot.start_s);
        // 2 px surface gap between touching segments (the gap separates, not a stroke).
        const width = Math.max(1, x(shot.end_s) - left - 2);
        const label = `${shot.idx + 1} ${motionGlyph(shot.motion)}`;
        const fits = width >= label.length * 6.5 + 10;
        return (
          <g key={shot.id}>
            <rect
              x={left + 1}
              y={band.top}
              width={width}
              height={band.height}
              rx={3}
              fill={FAMILY_FILL[motionFamily(shot.motion)]}
            />
            {fits ? (
              <text
                x={left + 1 + 6}
                y={band.top + band.height / 2 + 4}
                fontSize={11}
                fontWeight={600}
                fill={LABEL_INK}
                className="tabular-nums"
              >
                {label}
              </text>
            ) : null}
          </g>
        );
      })}
    </g>
  );
}

/**
 * Chapters as neutral segments (colour stays the camera motion's), told apart by a
 * 2 px gap and alternate shades, each labelled with its number and as much of its title as fits.
 */
function ChapterBand({
  chapters,
  x,
  band,
  hovered,
}: {
  chapters: SynthesisChapter[];
  x: LinearScale;
  band: Band;
  hovered: number | undefined;
}) {
  return (
    <g>
      {chapters.map((chapter, index) => {
        const left = x(chapter.start_s);
        const width = Math.max(1, x(chapter.end_s) - left - 2);
        const label = chapterLabel(chapter, width - 12);
        // Wide enough apart for the boundaries to read (about 3:1 between neighbours).
        const shade = index % 2 === 0 ? 0.22 : 0.12;
        return (
          <g key={chapter.index}>
            <rect
              x={left + 1}
              y={band.top}
              width={width}
              height={band.height}
              rx={3}
              fill="var(--foreground)"
              opacity={chapter.index === hovered ? shade + 0.1 : shade}
            />
            {label ? (
              <text
                x={left + 1 + 6}
                y={band.top + band.height / 2 + 4}
                fontSize={11}
                fontWeight={500}
                fill="var(--foreground)"
                className="tabular-nums"
              >
                {label}
              </text>
            ) : null}
          </g>
        );
      })}
    </g>
  );
}

/**
 * Suggested highlights in neutral ink: the picture as the mark, the extra sound of a J-cut or
 * an L-cut as a thinner, dimmer extension outside it, at least SOUND_STUB_PX long (real offsets
 * are 0.1 to 0.7 s: under a pixel on an 8-minute track); the rank inside when it fits.
 */
const SOUND_STUB_PX = 4;

function HighlightMarks({
  highlights,
  x,
  band,
  hovered,
}: {
  highlights: SynthesisHighlight[];
  x: LinearScale;
  band: Band;
  hovered: number | undefined;
}) {
  const middle = band.top + band.height / 2;
  return (
    <g>
      {highlights.map((highlight) => {
        const { picture_in_s: pictureIn, picture_out_s: pictureOut } = highlight.clip;
        const sound = soundRange(highlight.clip);
        const left = x(pictureIn);
        const width = Math.max(2, x(pictureOut) - left);
        const label = String(highlight.rank);
        const active = highlight.rank === hovered;
        return (
          <g key={highlight.rank}>
            {sound
              ? [
                  sound[0] < pictureIn
                    ? { key: "j", to: left, length: x(pictureIn) - x(sound[0]), before: true }
                    : null,
                  sound[1] > pictureOut
                    ? {
                        key: "l",
                        to: left + width,
                        length: x(sound[1]) - x(pictureOut),
                        before: false,
                      }
                    : null,
                ].map((stub) => {
                  if (!stub) return null;
                  const length = Math.max(SOUND_STUB_PX, stub.length);
                  return (
                    <rect
                      key={stub.key}
                      x={stub.before ? stub.to - length : stub.to}
                      y={middle - 1.5}
                      width={length}
                      height={3}
                      rx={1.5}
                      fill="var(--foreground)"
                      opacity={active ? 0.8 : 0.6}
                    />
                  );
                })
              : null}
            <rect
              x={left}
              y={band.top}
              width={width}
              height={band.height}
              rx={2}
              fill="var(--foreground)"
              opacity={active ? 0.95 : 0.75}
            />
            {width >= label.length * 7 + 6 ? (
              <text
                x={left + width / 2}
                y={band.top + band.height / 2 + 3.5}
                fontSize={10}
                fontWeight={600}
                textAnchor="middle"
                fill="var(--card)"
                className="tabular-nums"
              >
                {label}
              </text>
            ) : null}
          </g>
        );
      })}
    </g>
  );
}

function TrackPlaceholder({
  band,
  width,
  children,
}: {
  band: Band;
  width: number;
  children: string;
}) {
  return (
    <g>
      <rect x={0} y={band.top} width={width} height={band.height} rx={3} fill="var(--muted)" />
      <text x={8} y={band.top + band.height / 2 + 4} fontSize={11} fill="var(--muted-foreground)">
        {children}
      </text>
    </g>
  );
}

/** The only part of the timeline that re-renders on every animation frame during playback. */
function PlayheadMarker({ x, axisTop }: { x: LinearScale; axisTop: number }) {
  const time = usePlayheadTime();
  const px = x(time);
  return (
    <g className="pointer-events-none">
      <line x1={px} x2={px} y1={0} y2={axisTop} stroke="var(--primary)" strokeWidth={2} />
      <path
        d={`M${px - 5},${axisTop}L${px + 5},${axisTop}L${px},${axisTop - 6}Z`}
        fill="var(--primary)"
      />
    </g>
  );
}
