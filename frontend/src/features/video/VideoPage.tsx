import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";

import type { Keyframe } from "@/api/client";
import { errorMessage } from "@/api/client";
import {
  useKeyframes,
  useOcr,
  useShots,
  useSignals,
  useSubjects,
  useSynthesis,
  useTranscript,
  useVideo,
} from "@/api/queries";
import { VIDEO_TABS, videoRoute, type VideoTab } from "@/app/router";
import { spanIndexAt } from "@/components/charts/scale";
import { OrientationBadge } from "@/components/common";
import { VideoStatusBadge } from "@/components/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { lastLibrarySearch } from "@/features/library/lastSearch";
import { cn } from "@/lib/utils";

import { AnalyzeDialog } from "./AnalyzeDialog";
import { AudioTab } from "./AudioTab";
import { ContextChips } from "./context/ContextChips";
import { MissingAnalyses } from "./MissingAnalyses";
import { OfflineNotice } from "./OfflineNotice";
import { TranscriptTab } from "./TranscriptTab";
import { CaptureCard } from "./CaptureCard";
import { ContextTab } from "./ContextTab";
import { ExportsTab } from "./ExportsTab";
import { FrameDetails } from "./FrameDetails";
import { KeyframeGrid } from "./KeyframeGrid";
import {
  bindVideoElement,
  PLAYER_ELEMENT_ID,
  PlayerContext,
  PlayheadStore,
  type PlayerApi,
} from "./player";
import { ResolveCard } from "./ResolveCard";
import { ShotsTab } from "./ShotsTab";
import { SynthesisOverview } from "./synthesis/SynthesisOverview";
import { TechniqueTab } from "./TechniqueTab";
import { Timeline } from "./Timeline";
import { StagesCard, VideoFacts } from "./VideoFacts";

export function VideoPage() {
  const { t } = useTranslation();
  const { videoId } = videoRoute.useParams();
  const search = videoRoute.useSearch();
  const navigate = useNavigate({ from: videoRoute.fullPath });
  const video = useVideo(videoId);
  const keyframes = useKeyframes(videoId);
  const shots = useShots(videoId);
  const signals = useSignals(videoId);
  const captions = useTranscript(videoId);
  const ocr = useOcr(videoId);
  const subjects = useSubjects(videoId);
  const synthesis = useSynthesis(videoId);
  const [playhead] = useState(() => new PlayheadStore(search.t ?? 0));
  const [element, setElement] = useState<HTMLVideoElement | null>(null);
  const elementRef = useRef<HTMLVideoElement | null>(null);
  const attachVideo = useCallback((node: HTMLVideoElement | null) => {
    elementRef.current = node;
    setElement(node);
  }, []);
  const [selectedId, setSelectedId] = useState<string>();
  const [playbackError, setPlaybackError] = useState(false);

  const frames = useMemo(() => keyframes.data ?? [], [keyframes.data]);
  const frameTimes = useMemo(() => frames.map((frame) => frame.t_s), [frames]);
  // Keyframe shown at the playhead: re-renders only when it changes, not on every frame.
  const currentId = useSyncExternalStore(playhead.subscribe, () => {
    const index = spanIndexAt(frameTimes, playhead.get() + 0.01);
    return frames[Math.max(0, index)]?.id;
  });
  const current = frames.find((frame) => frame.id === currentId);
  const selected = frames.find((frame) => frame.id === selectedId) ?? current;

  useEffect(() => (element ? bindVideoElement(element, playhead) : undefined), [element, playhead]);

  const seek = useCallback(
    (time: number) => {
      playhead.set(time);
      if (elementRef.current) {
        elementRef.current.currentTime = time;
      }
    },
    [playhead],
  );

  useEffect(() => {
    if (search.t !== undefined) {
      seek(search.t);
    }
  }, [search.t, seek]);

  // A time in the address (a search result): the player seeks there once it can, that is when
  // it has mounted and read the file's metadata (the page shows a placeholder until then).
  useEffect(() => {
    const target = search.t;
    const node = elementRef.current;
    if (!element || !node || target === undefined) {
      return undefined;
    }
    const go = (): void => {
      seek(target);
    };
    if (node.readyState >= HTMLMediaElement.HAVE_METADATA) {
      go();
      return undefined;
    }
    node.addEventListener("loadedmetadata", go, { once: true });
    return () => {
      node.removeEventListener("loadedmetadata", go);
    };
  }, [element, search.t, seek]);

  const duration = video.data?.duration_s ?? 0;
  const playerApi = useMemo<PlayerApi>(
    () => ({ playhead, duration, seek }),
    [playhead, duration, seek],
  );

  const selectFrame = useCallback(
    (frame: Keyframe) => {
      setSelectedId(frame.id);
      seek(frame.t_s);
    },
    [seek],
  );

  if (video.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(video.error)}</AlertDescription>
      </Alert>
    );
  }
  if (!video.data) {
    return <Skeleton className="h-[60vh] rounded-xl" />;
  }
  const detail = video.data;
  const vertical = detail.orientation === "vertical";
  const tab: VideoTab = search.tab ?? "overview";
  const shotList = shots.data ?? [];

  return (
    <PlayerContext value={playerApi}>
      {/* minmax(0, 1fr): a long file name is truncated instead of widening the page on a phone. */}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="ghost" size="icon" asChild>
              <Link to="/library" search={lastLibrarySearch()} aria-label={t("video.back")}>
                <ArrowLeft className="size-4" />
              </Link>
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold" title={detail.filename}>
                {detail.title ?? detail.filename}
              </h1>
              <p className="text-muted-foreground truncate text-sm">
                {detail.root_label} · {detail.rel_path}
              </p>
              <ContextChips video={detail} />
            </div>
            <OrientationBadge orientation={detail.orientation} />
            <VideoStatusBadge status={detail.status} />
          </div>
          <AnalyzeDialog
            videoId={detail.id}
            missing={detail.missing_stages}
            outdated={detail.outdated_stages}
          />
        </div>

        {detail.status === "offline" ? <OfflineNotice video={detail} /> : null}
        <MissingAnalyses detail={detail} />

        <div className="grid grid-cols-[minmax(0,1fr)] gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
          <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-6">
            <div
              className={cn(
                "overflow-hidden rounded-xl border bg-black",
                vertical && "mx-auto max-w-md",
              )}
            >
              <video
                ref={attachVideo}
                id={PLAYER_ELEMENT_ID}
                src={detail.proxy_url ?? detail.stream_url}
                poster={detail.poster_url ?? undefined}
                controls
                preload="metadata"
                className={cn("w-full", vertical ? "max-h-[75vh]" : "aspect-video")}
                onSeeked={() => {
                  setSelectedId(undefined);
                }}
                onError={() => {
                  setPlaybackError(true);
                }}
              >
                <track
                  kind="captions"
                  src={captions.data?.vtt_url ?? undefined}
                  srcLang={captions.data?.language ?? undefined}
                  label={t("video.tabs.transcript")}
                />
              </video>
            </div>
            {detail.proxy_url ? (
              <p className="text-muted-foreground text-xs">
                {t("video.proxyNote", { reason: detail.playback_issue ?? "" })}
              </p>
            ) : detail.playback_issue ? (
              <Alert>
                <AlertDescription>
                  {t("video.proxyPending", { reason: detail.playback_issue })}
                </AlertDescription>
              </Alert>
            ) : playbackError ? (
              <Alert>
                <AlertDescription>{t("video.unsupported")}</AlertDescription>
              </Alert>
            ) : null}

            {duration > 0 ? (
              <Card className="py-4">
                <CardContent>
                  <Timeline
                    duration={duration}
                    shots={shotList}
                    keyframes={frames}
                    audio={signals.data?.audio ?? null}
                    silences={signals.data?.audio_stats?.silences ?? []}
                    hasAudio={detail.has_audio ?? false}
                    selectedKeyframeId={selected?.id}
                    onSelectKeyframe={selectFrame}
                    chapters={synthesis.data?.chapters}
                    highlights={synthesis.data?.highlights}
                  />
                </CardContent>
              </Card>
            ) : null}

            <Tabs
              value={tab}
              onValueChange={(value) => {
                if ((VIDEO_TABS as readonly string[]).includes(value)) {
                  void navigate({
                    search: (prev) => ({ ...prev, tab: value as VideoTab }),
                    replace: true,
                  });
                }
              }}
            >
              {/* The tabs scroll sideways on a phone (pb-1: room for the active tab's line). */}
              <div className="overflow-x-auto pb-1">
                <TabsList variant="line" className="min-w-max">
                  <TabsTrigger value="overview">
                    {t("video.tabs.overview")}
                    <span className="text-muted-foreground text-xs">
                      {detail.analysed_count}/{detail.keyframe_count}
                    </span>
                  </TabsTrigger>
                  <TabsTrigger value="shots">
                    {t("video.tabs.shots")}
                    <span className="text-muted-foreground text-xs">{shotList.length}</span>
                  </TabsTrigger>
                  <TabsTrigger value="transcript">{t("video.tabs.transcript")}</TabsTrigger>
                  <TabsTrigger value="audio">{t("video.tabs.audio")}</TabsTrigger>
                  <TabsTrigger value="technique">{t("video.tabs.technique")}</TabsTrigger>
                  <TabsTrigger value="context">{t("video.tabs.context")}</TabsTrigger>
                  <TabsTrigger value="exports">{t("video.tabs.exports")}</TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="overview" className="grid gap-6 pt-3">
                <SynthesisOverview videoId={detail.id} offline={detail.status === "offline"} />
                <section className="grid gap-3" aria-labelledby="keyframes-title">
                  <h2 id="keyframes-title" className="text-lg font-semibold">
                    {t("video.keyframes")}
                  </h2>
                  {frames.length > 0 ? (
                    <KeyframeGrid
                      frames={frames}
                      selectedId={selected?.id}
                      currentId={current?.id}
                      onSelect={selectFrame}
                    />
                  ) : (
                    <p className="text-muted-foreground text-sm">{t("video.keyframesEmpty")}</p>
                  )}
                </section>
              </TabsContent>
              <TabsContent value="shots" className="pt-3">
                <ShotsTab
                  shots={shotList}
                  keyframes={frames}
                  usability={synthesis.data?.usability}
                  suggestions={synthesis.data?.suggestions}
                />
              </TabsContent>
              <TabsContent value="transcript" className="pt-3">
                <TranscriptTab videoId={detail.id} />
              </TabsContent>
              <TabsContent value="audio" className="pt-3">
                <AudioTab videoId={detail.id} />
              </TabsContent>
              <TabsContent value="technique" className="pt-3">
                <TechniqueTab
                  duration={duration}
                  shots={shotList}
                  keyframes={frames}
                  signals={signals.data}
                  selectedId={selected?.id}
                  onSelectFrame={selectFrame}
                />
              </TabsContent>
              <TabsContent value="context" className="pt-3">
                <ContextTab video={detail} />
              </TabsContent>
              <TabsContent value="exports" className="pt-3">
                <ExportsTab videoId={detail.id} />
              </TabsContent>
            </Tabs>
          </div>
          <aside className="grid content-start gap-6">
            <FrameDetails
              key={selected?.id ?? "none"}
              frame={selected}
              ocr={ocr.data?.lines.filter((line) => line.keyframe_id === selected?.id)}
              subjects={subjects.data?.frames.find((f) => f.keyframe_id === selected?.id)?.subjects}
              subjectsPart={subjects.data}
              subjectsLoading={subjects.isPending}
              subjectsError={subjects.isError ? errorMessage(subjects.error) : null}
            />
            <CaptureCard video={detail} />
            <VideoFacts video={detail} />
            <ResolveCard links={detail.resolve} />
            <StagesCard video={detail} />
          </aside>
        </div>
      </div>
    </PlayerContext>
  );
}
