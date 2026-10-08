import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect } from "react";

import { queryKeys } from "./queries";

interface ServerEvent {
  id: number;
  type: string;
  job_id: string | null;
  video_id: string | null;
  data: Record<string, unknown>;
}

const EVENT_TYPES = [
  "job.started",
  "job.progress",
  "job.finished",
  "stage.started",
  "stage.finished",
  "stage.cached",
  "library.scanned",
  "worker.started",
  "sidecar.imported",
  "timeline.synced",
  "analysis.resumed",
] as const;

/** Batch invalidations so that a burst of events triggers a single refetch per query. */
class Invalidator {
  private readonly client: QueryClient;
  private pending = new Map<string, QueryKey>();
  private timer: number | undefined;

  constructor(client: QueryClient) {
    this.client = client;
  }

  add(key: QueryKey): void {
    this.pending.set(JSON.stringify(key), key);
    this.timer ??= window.setTimeout(() => {
      this.flush();
    }, 400);
  }

  flush(): void {
    const keys = [...this.pending.values()];
    this.pending.clear();
    this.timer = undefined;
    for (const key of keys) {
      void this.client.invalidateQueries({ queryKey: key });
    }
  }

  dispose(): void {
    if (this.timer !== undefined) {
      window.clearTimeout(this.timer);
    }
  }
}

/** Subscribe to the backend event stream and keep TanStack Query caches fresh. */
export function useLiveEvents(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const source = new EventSource("/api/v1/events");
    const invalidator = new Invalidator(queryClient);

    const onEvent = (message: MessageEvent<string>): void => {
      let event: ServerEvent;
      try {
        event = JSON.parse(message.data) as ServerEvent;
      } catch {
        return;
      }
      invalidator.add(queryKeys.jobs);
      if (event.type === "library.scanned") {
        invalidator.add(queryKeys.roots);
        invalidator.add(queryKeys.folders);
        invalidator.add(queryKeys.videosAll);
        invalidator.add(queryKeys.timelines); // the videos a timeline finds in the library
      }
      if (event.type === "analysis.resumed") {
        // Analyses left waiting for LM Studio queued again: their videos are « en file ».
        invalidator.add(queryKeys.videosAll);
        invalidator.add(queryKeys.roots);
      }
      if (event.type === "timeline.synced") {
        // A Resolve timeline brought in or updated: its files, the folders of files
        // added alone, and each video's links to Resolve.
        invalidator.add(queryKeys.timelines);
        invalidator.add(queryKeys.videosAll);
        invalidator.add(queryKeys.folders);
        invalidator.add(queryKeys.roots);
        invalidator.add(queryKeys.videoAll);
      }
      if (!event.video_id && event.type.startsWith("job.")) {
        // A timeline's update job (or a scan): « adding (12/98) » follows its progress.
        invalidator.add(queryKeys.timelines);
      }
      if (
        event.video_id &&
        event.type === "stage.started" &&
        (event.data.stage === "detections" || event.data.stage === "grounding")
      ) {
        invalidator.add(queryKeys.subjects(event.video_id)); // show "running" at once
      }
      if (event.video_id && event.type === "stage.started" && event.data.stage === "synthesis") {
        invalidator.add(queryKeys.synthesis(event.video_id)); // show "running" at once
      }
      if (event.video_id && event.type !== "job.progress") {
        invalidator.add(queryKeys.video(event.video_id));
        invalidator.add(queryKeys.keyframes(event.video_id));
        invalidator.add(queryKeys.videosAll);
      }
      // An analysis file taken over: every result appears at once, without a stage.finished.
      if (
        event.video_id &&
        (event.type === "stage.finished" || event.type === "sidecar.imported")
      ) {
        invalidator.add(queryKeys.shots(event.video_id));
        invalidator.add(queryKeys.signals(event.video_id));
        invalidator.add(queryKeys.metadataAll(event.video_id));
        invalidator.add(queryKeys.context(event.video_id));
        invalidator.add(queryKeys.track(event.video_id));
        invalidator.add(queryKeys.audio(event.video_id));
        invalidator.add(queryKeys.transcript(event.video_id));
        invalidator.add(queryKeys.ocr(event.video_id));
        invalidator.add(queryKeys.subjects(event.video_id));
        // Any stage: a new analysis makes the synthesis « out of date », a new transcript
        // moves the sound of its clips, new shots change their usability (recomputed on read).
        invalidator.add(queryKeys.synthesis(event.video_id));
      }
      if (
        (event.type === "stage.finished" && event.data.stage === "index") ||
        event.type === "library.scanned"
      ) {
        // The search index of a video was written again (or videos went away).
        invalidator.add(queryKeys.searchAll);
        invalidator.add(queryKeys.searchFacets);
      }
      if (event.type === "job.finished" && event.video_id) {
        // A cancelled or failed job may end without a stage.finished for the synthesis.
        invalidator.add(queryKeys.synthesis(event.video_id));
      }
      if (event.type === "job.finished" && !event.video_id) {
        // A calibration (or a scan) done: what the System page shows may have changed.
        invalidator.add(queryKeys.visionProfile);
        invalidator.add(queryKeys.doctor);
      }
      if (!event.video_id && (event.type === "job.started" || event.type === "job.finished")) {
        // A model bench started or ended: its page, and the model loaded again.
        invalidator.add(queryKeys.bench);
        invalidator.add(queryKeys.models);
      }
      if (event.type === "job.progress" && event.video_id) {
        // Progress of the current frame description: refresh keyframes as they arrive.
        invalidator.add(queryKeys.keyframes(event.video_id));
      }
    };

    for (const type of EVENT_TYPES) {
      source.addEventListener(type, onEvent);
    }
    return () => {
      for (const type of EVENT_TYPES) {
        source.removeEventListener(type, onEvent);
      }
      source.close();
      invalidator.dispose();
    };
  }, [queryClient]);
}
