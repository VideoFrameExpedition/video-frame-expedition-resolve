import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import createClient from "openapi-fetch";
import type { ReactNode } from "react";

import type * as ClientModule from "@/api/client";
import type {
  AskCitation,
  AskResult,
  AskSummary,
  ModelInfo,
  SearchFacets,
  SearchIndex,
} from "@/api/client";
import type { paths } from "@/api/schema";

import type { AskPageSearch } from "./askState";
import { AskPage } from "./AskPage";

// The real queries, client and stream reader, against a fake server (MSW).
vi.mock("@/api/client", async (original) => {
  const actual = await original<typeof ClientModule>();
  const fetch = (request: Request) => globalThis.fetch(request);
  return { ...actual, api: createClient<paths>({ baseUrl: "http://localhost", fetch }) };
});

const navigate = vi.fn();
let search: AskPageSearch = {};
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
vi.mock("@/app/router", () => ({ askRoute: { useSearch: () => search } }));

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
  kinds: [{ value: "shot", count: 8 }],
  devices: [],
  places: [{ value: "France", count: 2 }],
  subjects: [],
  shot_types: [],
  weather: [{ value: "snow", count: 1 }],
  light_phases: [],
  orientations: [],
  date_min: null,
  date_max: null,
  speech: false,
  rated: false,
  favorites: false,
  usability: false,
};

function model(overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    key: "qwen/qwen3-vl-4b",
    display_name: "Qwen3 VL 4B",
    type: "llm",
    vision: true,
    reasoning_options: [],
    loaded_instances: [{ id: "qwen/qwen3-vl-4b", context_length: 19456, parallel: 4 }],
    ...overrides,
  };
}

function citation(overrides: Partial<AskCitation> = {}): AskCitation {
  return {
    n: 1,
    video_id: "v1",
    filename: "vacances.mp4",
    title: "Vacances à Hyères",
    kind: "transcript",
    t_start: 45,
    t_end: 48.4,
    shot_idx: 2,
    timecode: "00:45",
    thumb_url: "/api/v1/media/v1/t2.jpg",
    excerpt: "On met le riz dans la casserole.",
    available: true,
    ...overrides,
  };
}

function result(overrides: Partial<AskResult> = {}): AskResult {
  return {
    id: "01a0e4c661f575dc97927e143604df49",
    question: "Où verse-t-on du riz ?",
    filters: {},
    language: "fr",
    model: "qwen/qwen3-vl-4b",
    status: "answered",
    answer: "On met le riz dans la casserole [1]. <b>pas de HTML</b>",
    citations: [citation()],
    passages: 6,
    visual_check: null,
    truncated: false,
    dropped_citations: 0,
    error: null,
    prompt_tokens: 1067,
    completion_tokens: 40,
    timings: { total_ms: 2100 },
    created_at: "2026-09-28T10:00:00Z",
    ...overrides,
  };
}

const encoder = new TextEncoder();
function sse(event: string, data: unknown): string {
  return `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
}

let chunks: string[] = [];
let hold = false; // keep the stream open until the request is aborted
let aborted = false;
let models: ModelInfo[] = [model()];
let offline = false;
let facets: SearchFacets = FACETS;
let history: AskSummary[] = [];
let stored: AskResult | null = null;
const asked: unknown[] = [];
const deleted: string[] = [];

const server = setupServer(
  http.post("http://localhost/api/v1/ask", async ({ request }) => {
    asked.push(await request.json());
    const body = new ReadableStream<Uint8Array>({
      async start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
          await new Promise((resolve) => setTimeout(resolve, 5));
        }
        if (!hold) {
          controller.close();
          return;
        }
        request.signal.addEventListener("abort", () => {
          aborted = true;
        });
      },
    });
    return new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });
  }),
  http.get("http://localhost/api/v1/search/facets", () => HttpResponse.json(facets)),
  http.get("http://localhost/api/v1/library/folders", () => HttpResponse.json([])),
  http.get("http://localhost/api/v1/system/lmstudio/models", () =>
    offline ? HttpResponse.error() : HttpResponse.json(models),
  ),
  http.get("http://localhost/api/v1/ask/history", () =>
    HttpResponse.json({ items: history, total: history.length, limit: 50, offset: 0 }),
  ),
  http.get("http://localhost/api/v1/ask/history/:id", () =>
    stored ? HttpResponse.json(stored) : HttpResponse.json({ status: 404 }, { status: 404 }),
  ),
  http.delete("http://localhost/api/v1/ask/history/:id", ({ params }) => {
    deleted.push(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),
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
  chunks = [];
  hold = false;
  aborted = false;
  models = [model()];
  offline = false;
  facets = FACETS;
  history = [];
  stored = null;
  asked.length = 0;
  deleted.length = 0;
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AskPage />
    </QueryClientProvider>,
  );
}

async function typeQuestion(text: string) {
  const user = userEvent.setup();
  const box = await screen.findByRole("textbox", { name: "Votre question" });
  await waitFor(() => {
    expect(screen.getByRole("button", { name: "Demander" })).toBeDisabled(); // nothing typed
  });
  await user.type(box, text);
  return user;
}

describe("AskPage", () => {
  it("streams the answer, then shows its sources as links to the moment", async () => {
    search = { weather: "snow" };
    const done = result();
    chunks = [
      sse("start", { id: done.id, model: "qwen/qwen3-vl-4b", vision: true, passages: 6,
                     slot_tokens: 4864, answer_tokens: 800, context_length: 19456, parallel: 4 }), // prettier-ignore
      sse("token", { text: "On met le riz " }),
      sse("token", { text: "dans la casserole [1]." }),
      sse("done", done),
    ];
    renderPage();
    const user = await typeQuestion("Où verse-t-on du riz ?");
    await user.keyboard("{Enter}");

    const source = await screen.findAllByRole("link", {
      name: "Source 1 : vacances.mp4 à 00:45",
    });
    expect(source).toHaveLength(2); // the mark in the text, and the chip
    for (const link of source) {
      expect(link).toHaveAttribute("href", "/videos/v1?t=45");
    }
    expect(asked).toEqual([
      {
        question: "Où verse-t-on du riz ?",
        filters: { kinds: [], weather: ["snow"], light_phase: [], subjects: [], shot_types: [] },
        visual_check: false,
      },
    ]);
    const answer = screen.getByRole("region", { name: "Réponse" });
    expect(within(answer).getByText(/pas de HTML/)).toBeVisible();
    expect(answer.querySelector("b")).toBeNull(); // the model's text is never HTML
    const chips = screen.getByRole("list", { name: "Sources" });
    expect(within(chips).getByText("Vacances à Hyères")).toBeVisible();
    expect(within(chips).getByText("@ 00:45")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Réponse terminée.");
    // The address now names the kept question.
    const update = navigate.mock.calls.at(-1)?.[0] as {
      search: (prev: AskPageSearch) => AskPageSearch;
    };
    expect(update.search({ weather: "snow" })).toEqual({ weather: "snow", id: done.id });
  });

  it("stops the model with the stop button, keeping what was written", async () => {
    hold = true;
    chunks = [
      sse("start", { id: "q1", model: "qwen/qwen3-vl-4b", vision: true, passages: 4,
                     slot_tokens: 4864, answer_tokens: 800, context_length: 19456, parallel: 4 }), // prettier-ignore
      sse("token", { text: "Un chat dort" }),
    ];
    renderPage();
    const user = await typeQuestion("Que fait le chat ?");
    await user.click(screen.getByRole("button", { name: "Demander" }));
    expect(await screen.findByText("Un chat dort")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Arrêter" }));
    expect(
      await screen.findByText(
        "Réponse interrompue : ce qui a été écrit est gardé dans l'historique.",
        { selector: "p:not([role])" },
      ),
    ).toBeVisible();
    expect(screen.getByText("Un chat dort")).toBeVisible();
    await waitFor(() => {
      expect(aborted).toBe(true); // the connection closed: the server stops the model
    });
    expect(screen.getByRole("button", { name: "Demander" })).toBeEnabled();
  });

  it("says why a question cannot be asked", async () => {
    models = [model({ loaded_instances: [] })];
    renderPage();
    expect(
      await screen.findByText(/Aucun modèle n'est chargé dans LM Studio : chargez-en un/),
    ).toBeVisible();
    await userEvent.setup().type(screen.getByRole("textbox"), "riz ?");
    expect(screen.getByRole("button", { name: "Demander" })).toBeDisabled();
    expect(screen.getByRole("switch", { name: "Vérification visuelle" })).toBeDisabled();
  });

  it("says when LM Studio cannot be reached", async () => {
    offline = true;
    renderPage();
    expect(
      await screen.findByText(
        "LM Studio est injoignable : démarrez son serveur local pour poser une question.",
      ),
    ).toBeVisible();
  });

  it("explains an empty index", async () => {
    facets = { ...FACETS, index: { ...INDEX, indexed: 0, passages: 0, vectors: 0 } };
    renderPage();
    expect(await screen.findByText("Aucune vidéo indexée")).toBeVisible();
  });

  it("shows an error sent by the server", async () => {
    chunks = [
      sse("error", {
        code: "no_model_loaded",
        title: "Aucun modèle chargé",
        detail: "Aucun modèle n'est chargé.",
        status: 503,
      }),
    ];
    renderPage();
    const user = await typeQuestion("riz ?");
    await user.keyboard("{Enter}");
    expect(
      await screen.findByText(/Chargez-en un \(par exemple qwen\/qwen3-vl-4b\) : l'application/),
    ).toBeVisible();
  });

  it("asks for the visual check when the loaded model reads images", async () => {
    const done = result({
      visual_check: {
        status: "done",
        verdict: "partly_confirmed",
        note: "On voit la casserole, pas le riz.",
        frames: [{ n: 1, t_s: 45, thumb_url: "/api/v1/media/v1/t2.jpg" }],
      },
    });
    chunks = [sse("checking", { frames: 1 }), sse("done", done)];
    renderPage();
    const user = await typeQuestion("riz ?");
    const toggle = screen.getByRole("switch", { name: "Vérification visuelle" });
    await waitFor(() => {
      expect(toggle).toBeEnabled(); // once LM Studio said the loaded model reads images
    });
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "Demander" }));
    const check = await screen.findByRole("region", { name: "Vérification visuelle" });
    expect(within(check).getByText("Confirmée en partie")).toBeVisible();
    expect(within(check).getByText("On voit la casserole, pas le riz.")).toBeVisible();
    expect(within(check).getByRole("img", { name: "Image de la source 1, à 00:45" })).toBeVisible();
    expect((asked[0] as { visual_check: boolean }).visual_check).toBe(true);
  });

  it("reopens, flags and deletes questions of the history", async () => {
    history = [
      { id: "01a0e4c661f575dc97927e143604df49", question: "Où verse-t-on du riz ?",
        status: "uncited", citations: 0, model: "qwen/qwen3-vl-4b",
        created_at: "2026-09-28T10:00:00Z" }, // prettier-ignore
    ];
    search = { id: "01a0e4c661f575dc97927e143604df49" };
    stored = result({
      status: "uncited",
      answer: "Un chat dort sur le canapé.",
      citations: [citation({ available: false })],
    });
    renderPage();
    expect(
      await screen.findByText(
        "Cette réponse ne cite aucun passage : elle ne vient peut-être pas de vos vidéos.",
      ),
    ).toBeVisible();
    const chips = screen.getByRole("list", { name: "Sources" });
    expect(within(chips).queryByRole("link")).toBeNull(); // the video left the library
    expect(within(chips).getByText(/retirée de la bibliothèque/)).toBeVisible();
    const item = screen.getByRole("button", { name: "Rouvrir « Où verse-t-on du riz ? »" });
    expect(item).toHaveAttribute("aria-current", "true");
    expect(within(item).getByText("sans citation")).toBeVisible();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Reposer la question" }));
    expect(screen.getByRole("textbox", { name: "Votre question" })).toHaveValue(
      "Où verse-t-on du riz ?",
    );
    await user.click(screen.getByRole("button", { name: "Supprimer « Où verse-t-on du riz ? »" }));
    await waitFor(() => {
      expect(deleted).toEqual(["01a0e4c661f575dc97927e143604df49"]);
    });
  });
});
