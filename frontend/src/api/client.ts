import createClient, { type Middleware } from "openapi-fetch";

import i18n from "@/i18n";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type Video = Schemas["VideoOut"];
export type VideoDetail = Schemas["VideoDetailOut"];
export type Keyframe = Schemas["KeyframeOut"];
export type KeyframeMetrics = Schemas["KeyframeMetricsOut"];
export type Shot = Schemas["ShotOut"];
export type ShotStory = Schemas["ShotStoryOut"];
export type StoryFrame = Schemas["StoryFrameOut"];
export type Signals = Schemas["SignalsOut"];
export type VisualSignals = Schemas["VisualSignalsOut"];
export type AudioCurve = Schemas["AudioCurveOut"];
export type AudioStats = Schemas["AudioStatsOut"];
export type Metadata = Schemas["MetadataOut"];
export type ExifSummary = Schemas["ExifSummaryOut"];
export type VideoContext = Schemas["ContextOut"];
export type PlaceContext = Schemas["PlaceOut"];
export type WeatherContext = Schemas["WeatherOut"];
export type SunContext = Schemas["SunOut"];
export type GpsPoint = Schemas["GpsPointOut"];
export type FrameAnalysis = Schemas["FrameAnalysis"];
export type Job = Schemas["JobOut"];
export type Root = Schemas["RootOut"];
export type DoctorReport = Schemas["DoctorReport"];
export type ModelInfo = Schemas["ModelInfo"];
export type VideoStatus = Schemas["VideoStatus"];
export type JobStatus = Schemas["JobStatus"];
export type StageStatus = Schemas["StageStatus"];
export type AnalysisMode = Schemas["AnalysisMode"];
export type StageInfo = Schemas["StageOut"];
export type StageFamily = Schemas["StageFamily"];
export type BatchAnalysis = Schemas["BatchAnalyzeOut"];
export type SidecarResult = Schemas["SidecarResultOut"];
export type SidecarStatus = Schemas["SidecarStatus"];
export type ExportOption = Schemas["ExportOptionOut"];
export type ExportFormat = Schemas["ExportFormat"];
export type LightPhase = Schemas["LightPhase"];
export type Synthesis = Schemas["SynthesisOut"];
export type SynthesisChapter = Schemas["SynthesisChapterOut"];
export type SynthesisHighlight = Schemas["SynthesisHighlightOut"];
export type SynthesisSuggestion = Schemas["SynthesisSuggestionOut"];
export type SynthesisTag = Schemas["SynthesisTagOut"];
export type ShotUsability = Schemas["ShotUsabilityOut"];
export type Clip = Schemas["ClipOut"];
export type WeatherConsensus = Schemas["WeatherConsensusOut"];
export type SearchResult = Schemas["SearchOut"];
export type SearchHit = Schemas["SearchHitOut"];
export type SearchIndex = Schemas["SearchIndexOut"];
export type SearchFacets = Schemas["SearchFacetsOut"];
export type FacetValue = Schemas["FacetValueOut"];
export type AskRequest = Schemas["AskRequest"];
export type AskFilters = Schemas["AskFilters"];
export type AskResult = Schemas["AskOut"];
export type AskCitation = Schemas["AskCitationOut"];
export type AskCheck = Schemas["AskCheckOut"];
export type AskStart = Schemas["AskStartOut"];
export type AskProblem = Schemas["AskErrorOut"];
export type AskSummary = Schemas["AskSummaryOut"];
export type AskStatus = AskResult["status"];
export type BenchOverview = Schemas["BenchOverview"];
export type BenchModel = Schemas["BenchModel"];
export type BenchRun = Schemas["BenchRunOut"];
export type BenchRunSummary = Schemas["BenchRunSummary"];
export type BenchModelRun = Schemas["BenchModelView"];
export type BenchScores = Schemas["BenchScores"];
export type BenchFrame = Schemas["BenchFrameOut"];
export type BenchAnswer = Schemas["BenchAnswer"];

/** RFC 9457 problem returned by the backend. */
interface Problem {
  title?: string;
  detail?: string;
  status?: number;
  code?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  static from(error: unknown, status: number): ApiError {
    const problem = (typeof error === "object" && error !== null ? error : {}) as Problem;
    return new ApiError(
      problem.status ?? status,
      problem.code ?? `http_${status}`,
      problem.detail ?? problem.title ?? `Erreur HTTP ${status}`,
    );
  }
}

/** Custom header required by the backend on every write (anti-CSRF, see SECURITY.md). */
const clientHeader: Middleware = {
  onRequest({ request }) {
    request.headers.set("X-VFE-Client", "web");
    return request;
  },
};

/** The interface's language: the analyses' texts come back in it. */
const languageHeader: Middleware = {
  onRequest({ request }) {
    request.headers.set("X-VFE-Language", i18n.language);
    return request;
  },
};

export const api = createClient<paths>({ baseUrl: "" });
api.use(clientHeader);
api.use(languageHeader);

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Resolve an openapi-fetch call to its data, throwing an ApiError on failure. */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || !response.ok) {
    throw ApiError.from(error, response.status);
  }
  return data as T;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
