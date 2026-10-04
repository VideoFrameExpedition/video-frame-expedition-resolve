import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

import { useLiveEvents } from "./events";

type Listener = (message: { data: string }) => void;

/** The server's event stream, driven by the test. */
class FakeEventSource {
  static current: FakeEventSource | undefined;
  private readonly listeners = new Map<string, Set<Listener>>();

  constructor() {
    FakeEventSource.current = this;
  }

  addEventListener(type: string, listener: Listener): void {
    const set = this.listeners.get(type) ?? new Set<Listener>();
    set.add(listener);
    this.listeners.set(type, set);
  }

  removeEventListener(type: string, listener: Listener): void {
    this.listeners.get(type)?.delete(listener);
  }

  close(): void {
    this.listeners.clear();
  }

  emit(type: string, event: Record<string, unknown>): void {
    const data = JSON.stringify({ id: 1, type, job_id: "j1", data: {}, ...event });
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ data });
    }
  }
}

function stream(): FakeEventSource {
  const source = FakeEventSource.current;
  if (!source) throw new Error("no event stream opened");
  return source;
}

function renderEvents() {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  renderHook(
    () => {
      useLiveEvents();
    },
    { wrapper },
  );
  /** The query keys invalidated since the last call (after the 400 ms batching). */
  return () => {
    vi.advanceTimersByTime(400);
    const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
    invalidate.mockClear();
    return keys;
  };
}

const SYNTHESIS = JSON.stringify(["synthesis", "v1"]);

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useLiveEvents and the synthesis", () => {
  it("reads the synthesis again when any stage of the video finishes", () => {
    const invalidated = renderEvents();
    // A new transcript moves the sound of the clips and makes the synthesis « out of date ».
    stream().emit("stage.finished", { video_id: "v1", data: { stage: "transcript" } });
    expect(invalidated()).toContain(SYNTHESIS);
    stream().emit("stage.finished", { video_id: "v1", data: { stage: "synthesis" } });
    expect(invalidated()).toContain(SYNTHESIS);
  });

  it("shows at once that a synthesis is being written", () => {
    const invalidated = renderEvents();
    stream().emit("stage.started", { video_id: "v1", data: { stage: "synthesis" } });
    expect(invalidated()).toContain(SYNTHESIS);
    stream().emit("stage.started", { video_id: "v1", data: { stage: "ocr" } });
    expect(invalidated()).not.toContain(SYNTHESIS);
  });

  it("reads it again when the video's job ends, whatever became of the stage", () => {
    const invalidated = renderEvents();
    stream().emit("job.finished", { video_id: "v1", data: { status: "cancelled" } });
    expect(invalidated()).toContain(SYNTHESIS);
  });
});

describe("useLiveEvents and an analysis file taken over", () => {
  it("shows every imported result at once, without waiting for a stage", () => {
    const invalidated = renderEvents();
    stream().emit("sidecar.imported", {
      video_id: "v1",
      data: { file: "clip.txt", keyframes: 3, stages: 17 },
    });
    const keys = invalidated();
    for (const key of ["shots", "transcript", "subjects", "synthesis", "keyframes", "video"]) {
      expect(keys).toContain(JSON.stringify([key, "v1"]));
    }
  });
});

describe("useLiveEvents and the Resolve timelines", () => {
  it("reads the timelines, their videos and the folders again once one is updated", () => {
    const invalidated = renderEvents();
    stream().emit("timeline.synced", {
      data: { bin_id: "b1", linked: 98, added: 2, missing: 0, outside: 0, errors: 0, queued: 3 },
    });
    const keys = invalidated();
    for (const key of [["timelines"], ["videos"], ["folders"], ["roots"], ["video"]]) {
      expect(keys).toContain(JSON.stringify(key));
    }
  });

  it("follows the progress of a timeline's update, not of a video's analysis", () => {
    const invalidated = renderEvents();
    const TIMELINES = JSON.stringify(["timelines"]);
    stream().emit("job.progress", { video_id: null, data: { progress: 0.12 } });
    expect(invalidated()).toContain(TIMELINES);
    stream().emit("job.progress", { video_id: "v1", data: { progress: 0.5 } });
    expect(invalidated()).not.toContain(TIMELINES);
  });
});
