import type { Schemas } from "@/api/client";

/** Test data shaped like the Resolve timeline endpoints: the project « cats 2026 »,
 * its timeline « Timeline 1 » (98 videos, some left out). */

const NOTHING_SKIPPED: Schemas["SkippedItemsOut"] = {
  graphics: 0,
  graphics_names: [],
  containers: 0,
  container_names: [],
  unsupported: 0,
  unsupported_names: [],
  not_video: 0,
  not_video_names: [],
  elsewhere: 0,
  elsewhere_names: [],
};

export const SKIPPED: Schemas["SkippedItemsOut"] = {
  ...NOTHING_SKIPPED,
  graphics: 2,
  graphics_names: ["Texte+", "Fond uni"],
  containers: 3,
  container_names: ["Multicam 1", "Composé 1", "Composé 2"],
  unsupported: 1,
  unsupported_names: ["A001.braw"],
  not_video: 2,
  not_video_names: ["musique.wav", "logo.png"],
};

const TIMELINE_REF: Schemas["ResolveTimelineRefOut"] = {
  id: "t1",
  name: "Timeline 1",
  fps: 29.97,
  drop_frame: false,
  start_timecode: "01:00:00:00",
  duration_s: 185,
};

export function resolveTimeline(
  id: string,
  name: string,
  extra: Partial<Schemas["ResolveTimelineOut"]> = {},
): Schemas["ResolveTimelineOut"] {
  return {
    id,
    name,
    fps: 25,
    drop_frame: false,
    start_timecode: "01:00:00:00",
    duration_s: 185,
    width: 3840,
    height: 2160,
    video_tracks: 1,
    audio_tracks: 1,
    video_clips: 12,
    is_current: false,
    bin_id: null,
    bin_label: null,
    synced_at: null,
    changed_since_sync: null,
    ...extra,
  };
}

export const PROJECT: Schemas["ResolveProjectOut"] = {
  product: "DaVinci Resolve Studio",
  version: "21.1.0.17",
  studio: true,
  database: { type: "Disk", name: "Local Database" },
  project: { id: "p1", name: "cats 2026" },
  current_timeline_id: "t1",
  timelines: [
    resolveTimeline("t1", "Timeline 1", { fps: 29.97, video_clips: 98, is_current: true }),
    resolveTimeline("t2", "Rushs du matin", {
      bin_id: "b2",
      bin_label: "Matin",
      synced_at: "2026-09-27T10:00:00Z",
      changed_since_sync: true,
    }),
  ],
};

export const PREVIEW: Schemas["TimelinePreviewOut"] = {
  database: PROJECT.database,
  project: PROJECT.project,
  timeline: TIMELINE_REF,
  studio: true,
  files: 98,
  in_library: 90,
  other_path: 0,
  in_folder: 0,
  new_folder: 6,
  new_folders: ["D:\\cats 2026\\hdr"],
  missing: 2,
  unknown: 0,
  disabled_only: 0,
  to_analyze: 8,
  skipped: SKIPPED,
  read_errors: 0,
  bin_id: null,
  bin_label: null,
  snapshot_id: "s1",
};

export function timelineBin(
  extra: Partial<Schemas["TimelineBinOut"]> = {},
): Schemas["TimelineBinOut"] {
  return {
    id: "b1",
    label: "Montage",
    database: PROJECT.database,
    project: PROJECT.project,
    timeline: TIMELINE_REF,
    auto_analyze: true,
    items: 98,
    videos: 95,
    states: {
      in_library: 95,
      adding: 0,
      not_processed: 0,
      removed: 0,
      missing: 0,
      outside: 0,
      error: 0,
    },
    report: { errors: 0, skipped: NOTHING_SKIPPED },
    sync_job: null,
    created_at: "2026-09-28T12:00:00Z",
    synced_at: "2026-09-28T12:31:00Z",
    ...extra,
  };
}
