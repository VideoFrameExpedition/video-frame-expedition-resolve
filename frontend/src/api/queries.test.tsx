import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { queryKeys, useImportTimeline, useVideos } from "./queries";

interface Query {
  limit: number;
  offset: number;
  timeline_bin_id?: string;
  sort?: string;
}
const TOTAL = 700;
// GET /videos: at most 500 per request, as the server does.
const GET = vi.fn((_path: string, init: { params: { query: Query } }) => {
  const { limit, offset } = init.params.query;
  if (limit > 500) {
    return Promise.resolve({ error: { detail: "limit" }, response: { ok: false, status: 422 } });
  }
  const count = Math.max(0, Math.min(limit, TOTAL - offset));
  const items = Array.from({ length: count }, (_, i) => ({ id: `v${String(offset + i)}` }));
  return Promise.resolve({
    data: { items, total: TOTAL, limit, offset },
    response: { ok: true, status: 200 },
  });
});
const POST = vi.fn();
vi.mock("./client", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  api: {
    GET: (path: string, init: { params: { query: Query } }) => GET(path, init),
    POST: (path: string, init: unknown) => POST(path, init) as unknown,
  },
}));

const withClient = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper };
};
const renderVideos = (
  limit: number,
  filters: Parameters<typeof useVideos>[0] = { root_id: "r1" },
) => renderHook(() => useVideos({ ...filters, limit }), withClient());
const asked = (): Query[] => GET.mock.calls.map(([, init]) => init.params.query);

beforeEach(() => {
  GET.mockClear();
});

describe("useVideos", () => {
  it("reads the first videos in one request", async () => {
    const { result } = renderVideos(120);
    await waitFor(() => {
      expect(result.current.data?.items).toHaveLength(120);
    });
    expect(result.current.data?.total).toBe(TOTAL);
    expect(asked()).toMatchObject([{ limit: 120, offset: 0 }]);
  });

  it("reads a list longer than the server gives at once in several requests", async () => {
    const { result } = renderVideos(600);
    await waitFor(() => {
      expect(result.current.data?.items).toHaveLength(600);
    });
    expect(result.current.data?.items.at(-1)?.id).toBe("v599");
    expect(asked()).toMatchObject([
      { limit: 500, offset: 0 },
      { limit: 100, offset: 500 },
    ]);
  });

  it("asks a Resolve timeline's videos in its order: no sort unless one is chosen", async () => {
    const { result } = renderVideos(10, { timeline_bin_id: "b1" });
    await waitFor(() => {
      expect(result.current.data?.items).toHaveLength(10);
    });
    expect(asked()[0]).toMatchObject({ timeline_bin_id: "b1" });
    expect(asked()[0]?.sort).toBeUndefined();
  });
});

describe("useImportTimeline", () => {
  it("puts the timeline added in the list at once, before the list is read again", async () => {
    const known = { id: "b1", label: "Montage" };
    const added = { id: "b2", label: "Chats" };
    POST.mockResolvedValue({
      data: { bin: added, created: true },
      response: { ok: true, status: 201 },
    });
    const { client, wrapper } = withClient();
    client.setQueryData(queryKeys.timelines, [known]);
    const { result } = renderHook(() => useImportTimeline(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ project_id: "p1", timeline_id: "t2", auto_analyze: true });
    });
    expect(POST.mock.calls[0]?.[0]).toBe("/api/v1/resolve/timelines/import");
    expect(client.getQueryData(queryKeys.timelines)).toEqual([known, added]);
  });
});
