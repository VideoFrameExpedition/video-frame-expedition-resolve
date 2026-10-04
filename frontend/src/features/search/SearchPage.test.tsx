import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import createClient from "openapi-fetch";
import type { ReactNode } from "react";

import type * as ClientModule from "@/api/client";
import type { SearchFacets, SearchHit, SearchIndex, SearchResult } from "@/api/client";
import type { paths } from "@/api/schema";

import type { SearchPageSearch } from "./searchState";
import { SearchPage } from "./SearchPage";

// The real queries and client, against a fake server (MSW): only the base URL differs, as
// Node's fetch needs an absolute address.
vi.mock("@/api/client", async (original) => {
  const actual = await original<typeof ClientModule>();
  // Looked up at each call: MSW replaces the global fetch once the server listens.
  const fetch = (request: Request) => globalThis.fetch(request);
  return { ...actual, api: createClient<paths>({ baseUrl: "http://localhost", fetch }) };
});

const navigate = vi.fn();
let search: SearchPageSearch = {};
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  Link: ({
    children,
    to,
    params,
    search: query,
    ...rest
  }: {
    children: ReactNode;
    to: string;
    params?: { videoId: string };
    search?: { t?: number };
  }) => {
    const path = params ? to.replace("$videoId", params.videoId) : to;
    const suffix = query?.t !== undefined ? `?t=${query.t.toString()}` : "";
    return (
      <a href={`${path}${suffix}`} {...rest}>
        {children}
      </a>
    );
  },
}));
vi.mock("@/app/router", () => ({ searchRoute: { useSearch: () => search } }));

const INDEX: SearchIndex = {
  videos: 2,
  indexed: 2,
  passages: 24,
  vectors: 24,
  semantic: true,
  model: "embeddings/embeddinggemma-300m-q4@599962c3",
};

const FACETS: SearchFacets = {
  index: INDEX,
  kinds: [
    { value: "video", count: 2 },
    { value: "shot", count: 8 },
  ],
  devices: [{ value: "Apple iPhone 15 Pro", count: 1 }],
  places: [{ value: "France", count: 2 }],
  subjects: [{ value: "chien", count: 1 }],
  shot_types: [{ value: "medium", count: 3 }],
  weather: [{ value: "snow", count: 1 }],
  light_phases: [{ value: "golden_hour", count: 1 }],
  orientations: [{ value: "vertical", count: 1 }],
  date_min: "2025-12-24",
  date_max: "2026-07-01",
  speech: true,
  rated: false,
  favorites: false,
  usability: true,
};

function hit(overrides: Partial<SearchHit>): SearchHit {
  return {
    chunk_id: 1,
    video_id: "v1",
    filename: "vacances.mp4",
    title: "Vacances à Hyères",
    kind: "transcript",
    t_start: 45,
    t_end: 48.4,
    shot_idx: 2,
    thumb_url: "/api/v1/media/v1/t2.jpg",
    snippet: "On met le riz dans la casserole.",
    highlights: [[10, 13]],
    score: 0.032,
    retrievers: ["words", "meaning"],
    captured_at: null,
    duration_s: 120,
    ...overrides,
  };
}

function result(hits: SearchHit[], overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    query: "riz",
    hits,
    total: hits.length,
    limit: 50,
    offset: 0,
    retrievers: ["words", "meaning"],
    note: null,
    index: INDEX,
    ...overrides,
  };
}

let answer: SearchResult = result([]);
let facets: SearchFacets = FACETS;
const asked: URLSearchParams[] = [];
const server = setupServer(
  http.get("http://localhost/api/v1/search", ({ request }) => {
    asked.push(new URL(request.url).searchParams);
    return HttpResponse.json(answer);
  }),
  http.get("http://localhost/api/v1/search/facets", () => HttpResponse.json(facets)),
  http.get("http://localhost/api/v1/library/folders", () => HttpResponse.json([])),
);

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});
afterAll(() => {
  server.close();
});
beforeEach(() => {
  navigate.mockReset();
  search = {};
  facets = FACETS;
  answer = result([]);
  asked.length = 0;
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SearchPage />
    </QueryClientProvider>,
  );
}

describe("SearchPage", () => {
  it("groups the passages by video, with their time, kind and matched words", async () => {
    search = { q: "riz" };
    answer = result(
      [
        hit({}),
        hit({ chunk_id: 2, video_id: "v2", filename: "montagne.mp4", title: null, kind: "video",
              t_start: null, t_end: null, shot_idx: null, snippet: "Riz au sommet", highlights: [[0, 3]],
              retrievers: ["meaning"] }), // prettier-ignore
        hit({ chunk_id: 3, kind: "shot", t_start: 40, t_end: 60, snippet: "RIZ BASMATI",
              highlights: [[0, 3]], retrievers: ["words"] }), // prettier-ignore
      ],
      { total: 3 },
    );
    renderPage();
    expect(await screen.findByText("3 passages trouvés par les mots et par le sens")).toBeVisible();
    expect(asked[0]?.get("q")).toBe("riz");
    expect(screen.getByRole("link", { name: "Vacances à Hyères" })).toHaveAttribute(
      "href",
      "/videos/v1",
    );
    const speech = screen.getByRole("link", { name: "Ouvrir vacances.mp4 à 00:45" });
    expect(speech).toHaveAttribute("href", "/videos/v1?t=45");
    expect(within(speech).getByText("Parole")).toBeVisible();
    expect(within(speech).getByText("00:45 – 00:48")).toBeVisible();
    expect(within(speech).getByText("riz").tagName).toBe("MARK");
    const shot = screen.getByRole("link", { name: "Ouvrir vacances.mp4 à 00:40" });
    expect(within(shot).getByText("Plan 3")).toBeVisible();
    // The other video, found by meaning: opened at its start, its file name as its title.
    const whole = screen.getByRole("link", { name: "Ouvrir montagne.mp4" });
    expect(whole).toHaveAttribute("href", "/videos/v2");
    expect(within(whole).getByText("sens")).toBeVisible();
    // Grouped by video, in the order of each one's best passage.
    expect(screen.getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "/videos/v1",
      "/videos/v1?t=45",
      "/videos/v1?t=40",
      "/videos/v2",
      "/videos/v2",
    ]);
  });

  it("sends the filters of the address, and offers those the index holds", async () => {
    search = { weather: "snow", speech: "no", root: "r1", folder: "Sommets" };
    answer = result([hit({ kind: "shot" })], { query: "", total: 1 });
    renderPage();
    await screen.findByText("1 passage trouvé");
    const sent = asked[0];
    expect(sent?.getAll("weather")).toEqual(["snow"]);
    expect(sent?.get("has_speech")).toBe("false");
    expect(sent?.get("root_id")).toBe("r1");
    expect(sent?.get("folder")).toBe("Sommets");
    const filters = screen.getByRole("group", { name: "Filtres de la recherche" });
    expect(within(filters).getByRole("combobox", { name: "Toute météo" })).toBeVisible();
    expect(within(filters).getByRole("combobox", { name: "Tous les sujets" })).toBeVisible();
    // Nothing to filter on: no control.
    expect(within(filters).queryByRole("combobox", { name: "Toutes les notes" })).toBeNull();
    expect(within(filters).queryByRole("combobox", { name: "Favoris ou non" })).toBeNull();
    expect(within(filters).getByLabelText("Tournées à partir du")).toHaveAttribute(
      "min",
      "2025-12-24",
    );

    const user = userEvent.setup();
    await user.click(within(filters).getByRole("button", { name: "Effacer les filtres" }));
    const update = navigate.mock.calls.at(-1)?.[0] as {
      search: (prev: SearchPageSearch) => SearchPageSearch;
    };
    expect(update.search({ q: "neige", weather: "snow" })).toEqual({ q: "neige" });
  });

  it("types into the address after a pause", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByRole("searchbox", { name: "Rechercher dans les vidéos" }), "lac");
    await waitFor(() => {
      expect(navigate).toHaveBeenCalled();
    });
    const update = navigate.mock.calls.at(-1)?.[0] as {
      search: (prev: SearchPageSearch) => SearchPageSearch;
      replace: boolean;
    };
    expect(update.replace).toBe(true);
    expect(update.search({ weather: "snow" })).toEqual({ weather: "snow", q: "lac" });
    expect(asked).toEqual([]); // nothing searched before the address says so
  });

  it("explains an empty index, and a search by words only", async () => {
    facets = { ...FACETS, index: { ...INDEX, indexed: 0, passages: 0, vectors: 0 } };
    search = { q: "chat" };
    answer = result([], { index: facets.index });
    const { unmount } = renderPage();
    expect(await screen.findByText("Aucune vidéo indexée")).toBeVisible();
    expect(screen.getByRole("link", { name: "Aller à la bibliothèque" })).toHaveAttribute(
      "href",
      "/library",
    );
    unmount();

    const wordsOnly = { ...INDEX, semantic: false, vectors: 0, indexed: 1, model: null };
    facets = { ...FACETS, index: wordsOnly };
    answer = result([], { index: wordsOnly, retrievers: ["words"] });
    renderPage();
    expect(await screen.findByText("Aucun résultat")).toBeVisible();
    expect(screen.getByText(/1 vidéos indexées sur 2/)).toBeVisible();
    expect(screen.getByText(/Recherche par mots seulement/)).toBeVisible();
    expect(screen.getByText(/vfe models search/)).toBeVisible();
  });

  it("invites to search before anything is typed", async () => {
    renderPage();
    expect(await screen.findByText(/Tapez des mots ou une description/)).toBeVisible();
    expect(asked).toEqual([]);
  });
});
