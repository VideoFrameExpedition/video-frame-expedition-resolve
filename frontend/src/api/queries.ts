import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  ApiError,
  unwrap,
  type AnalysisMode,
  type JobStatus,
  type LightPhase,
  type Schemas,
  type VideoStatus,
} from "./client";
import type { paths } from "./schema";

export interface VideoFilters {
  q?: string | undefined;
  status?: VideoStatus[] | undefined;
  root_id?: string | undefined;
  folder?: string | undefined;
  timeline_bin_id?: string | undefined; // a Resolve timeline of the library, never with a root
  light_phase?: string[] | undefined;
  sort?: "recent" | "name" | "duration" | "captured" | undefined;
  limit?: number;
  offset?: number;
}

/** What the search page asks: the text, and the filters that are set. */
export type SearchParams = NonNullable<paths["/api/v1/search"]["get"]["parameters"]["query"]> & {
  q: string;
};

export const queryKeys = {
  health: ["health"] as const,
  platform: ["platform"] as const,
  roots: ["roots"] as const,
  folders: ["folders"] as const,
  videos: (filters: VideoFilters) => ["videos", filters] as const,
  videosAll: ["videos"] as const,
  video: (id: string) => ["video", id] as const,
  videoAll: ["video"] as const,
  keyframes: (id: string) => ["keyframes", id] as const,
  shots: (id: string) => ["shots", id] as const,
  signals: (id: string) => ["signals", id] as const,
  context: (id: string) => ["context", id] as const,
  track: (id: string) => ["track", id] as const,
  audio: (id: string) => ["audio", id] as const,
  transcript: (id: string) => ["transcript", id] as const,
  ocr: (id: string) => ["ocr", id] as const,
  subjects: (id: string) => ["subjects", id] as const,
  synthesis: (id: string) => ["synthesis", id] as const,
  exports: (id: string) => ["exports", id] as const,
  metadata: (id: string, raw = false) => ["metadata", id, raw] as const,
  metadataAll: (id: string) => ["metadata", id] as const,
  jobs: ["jobs"] as const,
  doctor: ["doctor"] as const,
  stages: ["stages"] as const,
  visionProfile: ["vision-profile"] as const,
  analysisSettings: ["settings", "analysis"] as const,
  models: ["lmstudio-models"] as const,
  lmStudioLink: ["settings", "lmstudio"] as const,
  data: ["system", "data"] as const,
  preferences: ["preferences"] as const,
  search: (params: SearchParams) => ["search", params] as const,
  searchAll: ["search"] as const,
  searchFacets: ["search-facets"] as const,
  askHistory: ["ask-history"] as const,
  question: (id: string) => ["question", id] as const,
  timelines: ["timelines"] as const,
  timelineItems: (binId: string) => ["timelines", binId, "items"] as const,
  resolveProject: ["resolve", "project"] as const,
  timelinePreview: (projectId: string, timelineId: string) =>
    ["resolve", "preview", projectId, timelineId] as const,
  timelinePlan: (request: TimelineBuild) => ["timeline-plan", request] as const,
  bench: ["bench"] as const,
  benchOverview: ["bench", "overview"] as const,
  benchRuns: ["bench", "runs"] as const,
  benchRun: (id: string) => ["bench", "run", id] as const,
};

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => unwrap(api.GET("/api/v1/system/health")),
    refetchInterval: 30_000,
  });
}

/** The system of the application's computer: it does not change while the page is open. */
export function usePlatform() {
  return useQuery({
    queryKey: queryKeys.platform,
    queryFn: () => unwrap(api.GET("/api/v1/system/health")),
    select: (health) => health.platform,
    staleTime: Infinity,
  });
}

export function useRoots() {
  return useQuery({
    queryKey: queryKeys.roots,
    queryFn: () => unwrap(api.GET("/api/v1/library/roots")),
  });
}

// The API gives at most this many videos per request: a longer list is read in several.
const VIDEOS_PER_REQUEST = 500;

export function useVideos(filters: VideoFilters) {
  return useQuery({
    queryKey: queryKeys.videos(filters),
    queryFn: async () => {
      const limit = filters.limit ?? 120;
      const offset = filters.offset ?? 0;
      const read = (start: number, count: number) =>
        unwrap(
          api.GET("/api/v1/videos", {
            params: {
              query: {
                q: filters.q === "" ? undefined : filters.q,
                status: filters.status?.length ? filters.status : undefined,
                root_id: filters.root_id,
                folder: filters.folder,
                timeline_bin_id: filters.timeline_bin_id,
                light_phase: filters.light_phase?.length
                  ? (filters.light_phase as LightPhase[])
                  : undefined,
                sort: filters.sort, // none: the most recent first, a timeline in its order
                limit: Math.min(count, VIDEOS_PER_REQUEST),
                offset: start,
              },
            },
          }),
        );
      const page = await read(offset, limit);
      let { items, total } = page;
      while (items.length < limit && offset + items.length < total) {
        const next = await read(offset + items.length, limit - items.length);
        if (next.items.length === 0) break;
        items = [...items, ...next.items];
        total = next.total;
      }
      return { ...page, items, total, limit };
    },
    placeholderData: keepPreviousData,
  });
}

export function useVideo(videoId: string) {
  return useQuery({
    queryKey: queryKeys.video(videoId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/videos/{video_id}", { params: { path: { video_id: videoId } } })),
  });
}

export function useKeyframes(videoId: string) {
  return useQuery({
    queryKey: queryKeys.keyframes(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/keyframes", {
          params: { path: { video_id: videoId } },
        }),
      ),
  });
}

export function useShots(videoId: string) {
  return useQuery({
    queryKey: queryKeys.shots(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/shots", { params: { path: { video_id: videoId } } }),
      ),
  });
}

export function useSignals(videoId: string) {
  return useQuery({
    queryKey: queryKeys.signals(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/signals", { params: { path: { video_id: videoId } } }),
      ),
  });
}

export function useVideoContext(videoId: string) {
  return useQuery({
    queryKey: queryKeys.context(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/context", { params: { path: { video_id: videoId } } }),
      ),
  });
}

export function useAudio(videoId: string) {
  return useQuery({
    queryKey: queryKeys.audio(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/audio", { params: { path: { video_id: videoId } } }),
      ),
  });
}

export function useTranscript(videoId: string) {
  return useQuery({
    queryKey: queryKeys.transcript(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/transcript", {
          params: { path: { video_id: videoId } },
        }),
      ),
  });
}

export function useOcr(videoId: string) {
  return useQuery({
    queryKey: queryKeys.ocr(videoId),
    queryFn: () =>
      unwrap(api.GET("/api/v1/videos/{video_id}/ocr", { params: { path: { video_id: videoId } } })),
  });
}

export function useSubjects(videoId: string) {
  return useQuery({
    queryKey: queryKeys.subjects(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/subjects", {
          params: { path: { video_id: videoId } },
        }),
      ),
  });
}

/** Title, summary, chapters and highlights written for the video, with what code computes
 * again on each read: usability of the shots, editing suggestions, weather. */
export function useSynthesis(videoId: string) {
  return useQuery({
    queryKey: queryKeys.synthesis(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/synthesis", {
          params: { path: { video_id: videoId } },
        }),
      ),
  });
}

/** Passages of the library by words and by meaning (null: nothing to search yet). */
export function useSearch(params: SearchParams | null, limit = 50) {
  const query = params ? { ...params, limit } : null;
  return useQuery({
    queryKey: queryKeys.search(query ?? { q: "" }),
    queryFn: () => unwrap(api.GET("/api/v1/search", { params: { query: query ?? { q: "" } } })),
    enabled: query !== null,
    placeholderData: keepPreviousData,
  });
}

/** The values the search filters can offer, and the state of the index. */
export function useSearchFacets() {
  return useQuery({
    queryKey: queryKeys.searchFacets,
    queryFn: () => unwrap(api.GET("/api/v1/search/facets")),
    staleTime: 30_000,
  });
}

/** The questions asked about the library, the latest first. */
export function useAskHistory() {
  return useQuery({
    queryKey: queryKeys.askHistory,
    queryFn: () => unwrap(api.GET("/api/v1/ask/history", { params: { query: { limit: 50 } } })),
  });
}

/** A question asked before, its answer and its citations. */
export function useQuestion(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.question(id ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/ask/history/{question_id}", {
          params: { path: { question_id: id ?? "" } },
        }),
      ),
    enabled: id !== undefined,
    retry: false,
  });
}

export function useDeleteQuestion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.DELETE("/api/v1/ask/history/{question_id}", {
          params: { path: { question_id: id } },
        }),
      ),
    onSuccess: async (_data, id) => {
      queryClient.removeQueries({ queryKey: queryKeys.question(id) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.askHistory });
    },
  });
}

export function useAnalysisSettings() {
  return useQuery({
    queryKey: queryKeys.analysisSettings,
    queryFn: () => unwrap(api.GET("/api/v1/settings/analysis")),
  });
}

export function usePatchAnalysisSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      unwrap(api.PATCH("/api/v1/settings/analysis", { body })),
    onSuccess: async (data) => {
      queryClient.setQueryData(queryKeys.analysisSettings, data);
      await queryClient.invalidateQueries({ queryKey: queryKeys.doctor });
    },
  });
}

export function usePatchVideo(videoId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["VideoPatch"]) =>
      unwrap(
        api.PATCH("/api/v1/videos/{video_id}", { params: { path: { video_id: videoId } }, body }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.video(videoId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.transcript(videoId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.searchAll }); // a new title
    },
  });
}

export function useTrack(videoId: string) {
  return useQuery({
    queryKey: queryKeys.track(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/track", { params: { path: { video_id: videoId } } }),
      ),
  });
}

export function useMetadata(videoId: string, includeRaw = false) {
  return useQuery({
    queryKey: queryKeys.metadata(videoId, includeRaw),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/metadata", {
          params: { path: { video_id: videoId }, query: { include_raw: includeRaw } },
        }),
      ),
  });
}

export type JobOrder = "recent" | "queue" | "finished";

export interface JobListQuery {
  status: JobStatus[];
  kind?: Schemas["JobKind"][];
  order?: JobOrder; // queue: the order the worker takes them in; finished: last finished first
  limit: number;
  offset?: number;
}

/** A list of jobs with what each is about (kept fresh by the event stream, under the jobs key). */
export function useJobList(query: JobListQuery) {
  return useQuery({
    queryKey: [...queryKeys.jobs, "list", query] as const,
    queryFn: () => unwrap(api.GET("/api/v1/jobs", { params: { query } })),
    placeholderData: keepPreviousData,
  });
}

/** How many jobs run and wait, how the last day went, and the time the analyses still need. */
export function useJobsSummary() {
  return useQuery({
    queryKey: [...queryKeys.jobs, "summary"] as const,
    queryFn: () => unwrap(api.GET("/api/v1/jobs/summary")),
  });
}

export function useDoctor() {
  return useQuery({
    queryKey: queryKeys.doctor,
    queryFn: () => unwrap(api.GET("/api/v1/system/doctor")),
    staleTime: 60_000,
  });
}

/** The analysis stages, in execution order (fixed for a given app version). */
export function useStages() {
  return useQuery({
    queryKey: queryKeys.stages,
    queryFn: () => unwrap(api.GET("/api/v1/system/stages")),
    staleTime: Infinity,
  });
}

export function useVisionProfile() {
  return useQuery({
    queryKey: queryKeys.visionProfile,
    queryFn: () => unwrap(api.GET("/api/v1/system/vision-profile")),
    staleTime: 30_000,
  });
}

/** Recalibrate the loaded vision model (a job of a few seconds). */
export function useProbeVision() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/system/vision-profile/probe")),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

/** What the model bench can test: the vision models of LM Studio, the card, the library. */
export function useBenchOverview() {
  return useQuery({
    queryKey: queryKeys.benchOverview,
    queryFn: () => unwrap(api.GET("/api/v1/bench")),
    refetchInterval: 30_000,
  });
}

/** The runs of the model bench, the latest first. */
export function useBenchRuns() {
  return useQuery({
    queryKey: queryKeys.benchRuns,
    queryFn: () => unwrap(api.GET("/api/v1/bench/runs")),
  });
}

/** One run with its measures and answers; followed every few seconds while it is active (its
 * models' results arrive one by one). */
export function useBenchRun(runId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.benchRun(runId ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/v1/bench/runs/{run_id}", { params: { path: { run_id: runId ?? "" } } })),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? 5_000 : false;
    },
  });
}

export function useStartBench() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["BenchRequest"]) => unwrap(api.POST("/api/v1/bench/runs", { body })),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.bench }),
        queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
      ]),
  });
}

export function useDeleteBenchRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      unwrap(api.DELETE("/api/v1/bench/runs/{run_id}", { params: { path: { run_id: runId } } })),
    onSuccess: async (_data, runId) => {
      queryClient.removeQueries({ queryKey: queryKeys.benchRun(runId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.benchRuns });
    },
  });
}

/** The user's note on a run of the bench (null: none), shown in its history. */
export function useAnnotateBench() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, note }: { runId: string; note: string | null }) =>
      unwrap(
        api.PATCH("/api/v1/bench/runs/{run_id}", {
          params: { path: { run_id: runId } },
          body: { note },
        }),
      ),
    onSuccess: (_data, { runId }) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.benchRun(runId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.benchRuns }),
      ]),
  });
}

/** A blind rating: shown at once, sent to the server, taken back if the server refuses. The
 * history follows: its measures carry the mean of the ratings. */
export function useRateBench(runId: string) {
  const queryClient = useQueryClient();
  const key = queryKeys.benchRun(runId);
  return useMutation({
    mutationFn: (body: Schemas["BenchRatingRequest"]) =>
      unwrap(
        api.PUT("/api/v1/bench/runs/{run_id}/ratings", {
          params: { path: { run_id: runId } },
          body,
        }),
      ),
    onMutate: (body) => {
      queryClient.setQueryData<Schemas["BenchRunOut"]>(key, (run) => {
        if (!run) return run;
        const marks = Object.fromEntries(
          Object.entries(run.ratings[body.keyframe_id] ?? {}).filter(
            ([model]) => model !== body.model,
          ),
        );
        if (body.rating !== null) {
          marks[body.model] = body.rating;
        }
        return { ...run, ratings: { ...run.ratings, [body.keyframe_id]: marks } };
      });
    },
    onError: () => queryClient.invalidateQueries({ queryKey: key }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.benchRuns }),
  });
}

export function useLmModels() {
  return useQuery({
    queryKey: queryKeys.models,
    queryFn: () => unwrap(api.GET("/api/v1/system/lmstudio/models")),
    refetchInterval: 30_000,
    retry: false,
  });
}

/** The model server (LM Studio, or an OpenAI-compatible server such as vLLM): the address in
 * use, its kind and settings, and the ones used before. */
export function useLmStudioLink() {
  return useQuery({
    queryKey: queryKeys.lmStudioLink,
    queryFn: () => unwrap(api.GET("/api/v1/settings/lmstudio")),
  });
}

/** Everything read from the model server is read again once its address changed. */
function useLmStudioMoved() {
  const queryClient = useQueryClient();
  return async (link: Schemas["LmStudioLinkOut"]): Promise<void> => {
    queryClient.setQueryData(queryKeys.lmStudioLink, link);
    await Promise.all(
      [queryKeys.models, queryKeys.doctor, queryKeys.visionProfile, queryKeys.bench].map(
        (queryKey) => queryClient.invalidateQueries({ queryKey }),
      ),
    );
  };
}

/** Talk to the model server at this address from now on (no address: the installation's own). */
export function useChooseLmStudio() {
  const moved = useLmStudioMoved();
  return useMutation({
    mutationFn: (body: Schemas["LmStudioChoice"]) =>
      unwrap(api.PUT("/api/v1/settings/lmstudio", { body })),
    onSuccess: moved,
  });
}

/** Whether a model server answers at an address, and which kind, without choosing it. */
export function useTestLmStudio() {
  return useMutation({
    mutationFn: (body: Schemas["LmStudioChoice"]) =>
      unwrap(api.POST("/api/v1/settings/lmstudio/test", { body })),
  });
}

/** The database as a whole: its size, the import or the reset waiting for the restart, and
 * what the last start carried out. */
export function useDataState() {
  return useQuery({
    queryKey: queryKeys.data,
    queryFn: () => unwrap(api.GET("/api/v1/system/data")),
  });
}

/** Prepare an archive of the database (and, at will, of the frames): its link
 * (``exportUrl``) downloads it once. */
export function useExportData() {
  return useMutation({
    mutationFn: (images: boolean) =>
      unwrap(api.POST("/api/v1/system/data/export", { body: { images } })),
  });
}

export function exportUrl(token: string): string {
  return `/api/v1/system/data/export/${token}`;
}

/** Send an export (or a database of the backups folder) as it is: checked, it replaces the
 * library at the next start. */
export function useImportData() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, keepSettings }: { file: File; keepSettings: boolean }) =>
      unwrap(
        api.POST("/api/v1/system/data/import", {
          params: { query: { name: file.name, keep_settings: keepSettings } },
          body: file as unknown as string,
          bodySerializer: (body: unknown) => body as BodyInit,
          headers: { "Content-Type": "application/octet-stream" },
        }),
      ),
    onSuccess: (pending) => {
      queryClient.setQueryData<Schemas["DataOut"]>(queryKeys.data, (data) =>
        data ? { ...data, pending } : data,
      );
    },
  });
}

/** Erase the library, the settings, or both, at the next start. */
export function useResetData() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (choice: Schemas["ResetChoice"]) =>
      unwrap(api.POST("/api/v1/system/data/reset", { body: choice })),
    onSuccess: (pending) => {
      queryClient.setQueryData<Schemas["DataOut"]>(queryKeys.data, (data) =>
        data ? { ...data, pending } : data,
      );
    },
  });
}

/** Drop the import or the reset waiting for the next start. */
export function useCancelPendingData() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/system/data/pending")),
    onSuccess: () => {
      queryClient.setQueryData<Schemas["DataOut"]>(queryKeys.data, (data) =>
        data ? { ...data, pending: null } : data,
      );
    },
  });
}

/** Stop the application (analyses back in the queue) and start it again in its window. */
export function useRestart() {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/system/restart")),
  });
}

/** Drop one of the past connections. */
export function useForgetLmStudio() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (url: string) =>
      unwrap(api.DELETE("/api/v1/settings/lmstudio/past", { params: { query: { url } } })),
    onSuccess: (link) => {
      queryClient.setQueryData(queryKeys.lmStudioLink, link);
    },
  });
}

/** Every root with its folders, as bins. */
export function useFolders() {
  return useQuery({
    queryKey: queryKeys.folders,
    queryFn: () => unwrap(api.GET("/api/v1/library/folders")),
  });
}

/** Take offline videos out of the library, with their analyses (the others are left). */
export function useForgetVideos() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (videoIds: string[]) =>
      unwrap(api.POST("/api/v1/videos/forget", { body: { video_ids: videoIds } })),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.videosAll }),
        queryClient.invalidateQueries({ queryKey: queryKeys.roots }),
        queryClient.invalidateQueries({ queryKey: queryKeys.folders }),
        queryClient.invalidateQueries({ queryKey: queryKeys.timelines }),
        queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
      ]),
  });
}

/** « Relink… »: look for these offline videos' files in a folder (a job). */
export function useRelinkVideos() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["RelinkRequest"]) =>
      unwrap(api.POST("/api/v1/videos/relink", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

/** Open the desktop's folder dialog on this computer (null: closed without choosing). */
export function usePickFolder() {
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/system/pick-folder")),
  });
}

export function usePatchRoot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rootId, body }: { rootId: string; body: Schemas["RootUpdate"] }) =>
      unwrap(
        api.PATCH("/api/v1/library/roots/{root_id}", {
          params: { path: { root_id: rootId } },
          body,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.roots }),
  });
}

export function useAddRoot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      path: string;
      label?: string | null;
      analysis_focus?: string | null;
      recursive: boolean;
      auto_analyze: boolean;
    }) => unwrap(api.POST("/api/v1/library/roots", { body })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.roots });
      await queryClient.invalidateQueries({ queryKey: queryKeys.folders });
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });
}

export function useScanRoot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rootId: string) =>
      unwrap(
        api.POST("/api/v1/library/roots/{root_id}/scan", {
          params: { path: { root_id: rootId } },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

/** Translate the analyses already made into French and English. */
export function useTranslateLibrary() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/library/translate")),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
    },
  });
}

export function useAnalyzeRoot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ rootId, mode }: { rootId: string; mode: AnalysisMode }) =>
      unwrap(
        api.POST("/api/v1/library/roots/{root_id}/analyze", {
          params: { path: { root_id: rootId } },
          body: { mode },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      await queryClient.invalidateQueries({ queryKey: queryKeys.videosAll });
    },
  });
}

export function useRemoveRoot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rootId: string) =>
      unwrap(
        api.DELETE("/api/v1/library/roots/{root_id}", { params: { path: { root_id: rootId } } }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.roots });
      await queryClient.invalidateQueries({ queryKey: queryKeys.folders });
      await queryClient.invalidateQueries({ queryKey: queryKeys.videosAll });
    },
  });
}

export function useAnalyze(videoId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { mode: AnalysisMode; focus?: string | null; stages?: string[] | null }) =>
      unwrap(
        api.POST("/api/v1/videos/{video_id}/analyze", {
          params: { path: { video_id: videoId } },
          body,
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      await queryClient.invalidateQueries({ queryKey: queryKeys.video(videoId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.synthesis(videoId) });
    },
  });
}

/** Several videos at once, for the chosen stages (null: every stage). */
export function useAnalyzeVideos() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { video_ids: string[]; stages: string[] | null; mode: AnalysisMode }) =>
      unwrap(api.POST("/api/v1/videos/analyze", { body })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs });
      await queryClient.invalidateQueries({ queryKey: queryKeys.videosAll });
    },
  });
}

/** The export formats of a video, and why one is not available yet. */
export function useExports(videoId: string) {
  return useQuery({
    queryKey: queryKeys.exports(videoId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/videos/{video_id}/exports", {
          params: { path: { video_id: videoId } },
        }),
      ),
  });
}

/** A CSV of these videos, one row each: the file comes back to the browser, nothing
 * is written on the disk. */
export function useExportCsv() {
  return useMutation({
    mutationFn: async (videoIds: string[]) => {
      const { data, error, response } = await api.POST("/api/v1/videos/export-csv", {
        body: { video_ids: videoIds },
        parseAs: "blob",
      });
      if (error !== undefined || !response.ok || !(data instanceof Blob)) {
        throw ApiError.from(error, response.status);
      }
      return { blob: data, filename: attachmentName(response, "videos.csv") };
    },
  });
}

/** « Create a timeline »: the ticked videos, in the order and the format asked. */
export type TimelineBuild = Schemas["TimelineBuildRequest"];

/** What the timeline of the ticked videos would be; nothing is written, Resolve is not read. The
 * last answer stays shown while another option is asked. */
export function useTimelinePlan(request: TimelineBuild) {
  return useQuery({
    queryKey: queryKeys.timelinePlan(request),
    queryFn: ({ signal }) =>
      unwrap(api.POST("/api/v1/videos/timeline-preview", { body: request, signal })),
    placeholderData: keepPreviousData,
    retry: false,
  });
}

/** The timeline's files in a ZIP: OTIO for DaVinci Resolve (File › Import ›
 * Timeline), FCPXML for Final Cut Pro, a SubRip file per subtitle track, LISEZ-MOI.txt. */
export function useExportTimeline() {
  return useMutation({
    mutationFn: async (request: TimelineBuild) => {
      const { data, error, response } = await api.POST("/api/v1/videos/export-timeline", {
        body: request,
        parseAs: "blob",
      });
      if (error !== undefined || !response.ok || !(data instanceof Blob)) {
        throw ApiError.from(error, response.status);
      }
      return { blob: data, filename: attachmentName(response, "timeline.zip") };
    },
  });
}

/** The timeline built in the project open in DaVinci Resolve (up to a few minutes). */
export function useBuildResolveTimeline() {
  return useMutation({
    mutationFn: (request: TimelineBuild) =>
      unwrap(api.POST("/api/v1/resolve/timelines", { body: request })),
    retry: false,
  });
}

/** The file name a download response gives (RFC 6266: ``filename*`` first). */
export function attachmentName(response: Response, fallback: string): string {
  const header = response.headers.get("Content-Disposition") ?? "";
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      // a malformed name: the plain one below
    }
  }
  return /filename="([^"]+)"/i.exec(header)?.[1] ?? fallback;
}

/** Write the analysis files (« <name>_FR.txt », « <name>_EN.txt ») next to these videos now. */
export function useExportSidecars() {
  return useMutation({
    mutationFn: (videoIds: string[]) =>
      unwrap(api.POST("/api/v1/videos/export-sidecars", { body: { video_ids: videoIds } })),
  });
}

export function useCancelJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/cancel", { params: { path: { job_id: jobId } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

export function useRetryJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      unwrap(api.POST("/api/v1/jobs/{job_id}/retry", { params: { path: { job_id: jobId } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
  });
}

/** At most this many active jobs are counted (« 500+ » beyond). */
export const ACTIVE_JOBS_LIMIT = 500;

/** The jobs queued or running (kept fresh by the event stream, under the jobs key). */
export function useActiveJobs() {
  return useQuery({
    queryKey: [...queryKeys.jobs, "active"] as const,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/jobs", {
          params: { query: { status: ["queued", "running"], limit: ACTIVE_JOBS_LIMIT } },
        }),
      ),
  });
}

/** « Stop all »: every queued job is cancelled, every running one stops. */
export function useCancelAllJobs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/jobs/cancel", { body: {} })),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
        queryClient.invalidateQueries({ queryKey: queryKeys.videosAll }),
      ]),
  });
}

/** The DaVinci Resolve timelines brought into the library, with the state of their
 * files and their last update. */
export function useTimelineBins() {
  return useQuery({
    queryKey: queryKeys.timelines,
    queryFn: () => unwrap(api.GET("/api/v1/library/timelines")),
  });
}

/** The files of a library timeline in its order, with their state (asked when shown). */
export function useTimelineItems(binId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.timelineItems(binId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/library/timelines/{bin_id}/items", {
          params: { path: { bin_id: binId } },
        }),
      ),
    enabled,
  });
}

/** The project open in DaVinci Resolve and its timelines. Each read asks Resolve again (from a
 * fraction of a second to several): read when the dialog opens, never kept once it closes, and
 * never retried on its own — the error says what to do, « Try again » is the user's call. */
export function useResolveProject(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.resolveProject,
    queryFn: ({ signal }) => unwrap(api.GET("/api/v1/resolve/project", { signal })),
    enabled,
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** What adding a timeline would do, read in Resolve without writing anything (the server keeps
 * this read two minutes for the import that follows: ``snapshot_id``). */
export function useTimelinePreview(projectId: string | undefined, timelineId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.timelinePreview(projectId ?? "", timelineId ?? ""),
    queryFn: ({ signal }) =>
      unwrap(
        api.GET("/api/v1/resolve/timelines/{timeline_id}/preview", {
          params: { path: { timeline_id: timelineId ?? "" }, query: { project_id: projectId } },
          signal,
        }),
      ),
    enabled: projectId !== undefined && timelineId !== undefined,
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

type TimelineBin = Schemas["TimelineBinOut"];

/** The library timelines with ``bin`` in (added or updated), before the list is read again: a
 * page opened on it at once never finds it missing. */
function withBin(bins: TimelineBin[] | undefined, bin: TimelineBin): TimelineBin[] | undefined {
  if (bins === undefined) return undefined;
  return bins.some((item) => item.id === bin.id)
    ? bins.map((item) => (item.id === bin.id ? bin : item))
    : [...bins, bin];
}

/** Everything a timeline added or updated can change: its files come into the library as the
 * update job registers them (the event stream follows it). */
async function invalidateTimelineViews(queryClient: ReturnType<typeof useQueryClient>) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.timelines }),
    queryClient.invalidateQueries({ queryKey: queryKeys.videosAll }),
    queryClient.invalidateQueries({ queryKey: queryKeys.folders }),
    queryClient.invalidateQueries({ queryKey: queryKeys.roots }),
    queryClient.invalidateQueries({ queryKey: queryKeys.jobs }),
  ]);
}

/** « Import the timeline » / « Update »: read in Resolve (or the preview's read), then a
 * job registers its files and asks for what is missing of their analyses. */
export function useImportTimeline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["TimelineImportRequest"]) =>
      unwrap(api.POST("/api/v1/resolve/timelines/import", { body })),
    retry: false,
    onSuccess: async (result) => {
      queryClient.setQueryData<TimelineBin[]>(queryKeys.timelines, (bins) =>
        withBin(bins, result.bin),
      );
      await invalidateTimelineViews(queryClient);
    },
  });
}

/** « Update from Resolve »: the timeline read again (its project must be open). */
export function useSyncTimeline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (binId: string) =>
      unwrap(
        api.POST("/api/v1/library/timelines/{bin_id}/sync", {
          params: { path: { bin_id: binId } },
        }),
      ),
    retry: false,
    onSuccess: async (result) => {
      queryClient.setQueryData<TimelineBin[]>(queryKeys.timelines, (bins) =>
        withBin(bins, result.bin),
      );
      await invalidateTimelineViews(queryClient);
    },
  });
}

export function useUpdateTimelineBin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ binId, body }: { binId: string; body: Schemas["TimelineBinPatch"] }) =>
      unwrap(
        api.PATCH("/api/v1/library/timelines/{bin_id}", {
          params: { path: { bin_id: binId } },
          body,
        }),
      ),
    onSuccess: async (bin) => {
      queryClient.setQueryData<TimelineBin[]>(queryKeys.timelines, (bins) => withBin(bins, bin));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.timelines }),
        queryClient.invalidateQueries({ queryKey: queryKeys.videoAll }), // its label on a video
      ]);
    },
  });
}

/** « Remove from the library »: the timeline only; no video or analysis is forgotten and
 * nothing changes in Resolve. */
export function useDeleteTimelineBin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (binId: string) =>
      unwrap(
        api.DELETE("/api/v1/library/timelines/{bin_id}", { params: { path: { bin_id: binId } } }),
      ),
    onSuccess: async (_data, binId) => {
      queryClient.setQueryData<TimelineBin[]>(queryKeys.timelines, (bins) =>
        bins?.filter((item) => item.id !== binId),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.timelines }),
        queryClient.invalidateQueries({ queryKey: queryKeys.videosAll }), // their timeline badge
        queryClient.invalidateQueries({ queryKey: queryKeys.videoAll }), // their Resolve links
      ]);
    },
  });
}
