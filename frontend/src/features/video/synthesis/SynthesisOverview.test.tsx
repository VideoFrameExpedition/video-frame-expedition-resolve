import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Synthesis } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";

import { PlayerContext, PlayheadStore, type PlayerApi } from "../player";
import { EMPTY, READY } from "./fixtures";
import { SynthesisOverview } from "./SynthesisOverview";

const synthesis = vi.fn<() => unknown>();
const mutate = vi.fn();
vi.mock("@/api/queries", () => ({
  useSynthesis: () => synthesis(),
  useAnalyze: () => ({ mutate, isPending: false }),
}));

const REGENERATE = { stages: ["synthesis"], mode: "full" };

function loaded(data: Synthesis) {
  synthesis.mockReturnValue({ isPending: false, isError: false, data });
}

function renderOverview({ offline = false } = {}) {
  const playhead = new PlayheadStore(0);
  const seek = vi.fn((t: number) => {
    playhead.set(t);
  });
  const api: PlayerApi = { playhead, duration: 10, seek };
  render(
    <TooltipProvider>
      <PlayerContext value={api}>
        <SynthesisOverview videoId="v1" offline={offline} />
      </PlayerContext>
    </TooltipProvider>,
  );
  return { seek };
}

beforeEach(() => {
  synthesis.mockReset();
  mutate.mockReset();
});

describe("SynthesisCard", () => {
  it("shows the generated texts, with where each keyword comes from", () => {
    loaded(READY);
    renderOverview();
    expect(screen.getByRole("heading", { name: "Le héron du lac" })).toBeVisible();
    expect(screen.getByText("Un héron se pose, puis s'envole.")).toBeVisible();
    expect(screen.getByText(/Au bord d'un lac/)).toBeVisible();
    const tags = screen.getByRole("list", { name: "Mots-clés" });
    expect(within(tags).getByText("héron").closest("li")).toHaveTextContent(
      "héron (écrit par le modèle)",
    );
    expect(within(tags).getByText("lac").closest("li")).toHaveAttribute(
      "title",
      "vu sur les images",
    );
    expect(within(tags).getByText("cri d'oiseau").closest("li")).toHaveTextContent("(entendu)");
    expect(
      screen.getByText("Générée par qwen/qwen3-vl-4b — peut contenir des erreurs"),
    ).toBeVisible();
    // Up to date: no « out of date ».
    expect(screen.queryByRole("button", { name: /À régénérer/ })).not.toBeInTheDocument();
  });

  it("shows the weather both sources agree on, the full line on focus", async () => {
    loaded(READY);
    renderOverview();
    const badge = screen.getByRole("button", {
      name: "Météo : Partiellement nuageux · confiance haute",
    });
    expect(badge).toHaveAccessibleDescription(/Open-Meteo et les images concordent\..*nuages 40 %/);
    act(() => {
      badge.focus();
    });
    expect(await screen.findByRole("tooltip")).toHaveTextContent(READY.weather?.line ?? "");
  });

  it("leaves the weather out when neither source knows it", () => {
    loaded({
      ...READY,
      weather: { category: null, agreement: "unknown", confidence: "low", line: "météo inconnue" },
    });
    renderOverview();
    expect(screen.queryByRole("button", { name: /Météo/ })).not.toBeInTheDocument();
  });

  it("flags a synthesis written before newer analyses", () => {
    loaded({ ...READY, stale: true });
    renderOverview();
    expect(screen.getByRole("button", { name: "À régénérer" })).toHaveAccessibleDescription(
      "Écrite avant de nouvelles analyses : « Régénérer » en tient compte.",
    );
  });

  it("asks before regenerating, then queues the synthesis stage in full mode", async () => {
    const user = userEvent.setup();
    loaded(READY);
    renderOverview();
    await user.click(screen.getByRole("button", { name: "Régénérer" }));
    const dialog = screen.getByRole("dialog", { name: "Régénérer la synthèse ?" });
    expect(dialog).toHaveTextContent("Votre titre et votre résumé ne changent pas.");
    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));
    expect(mutate).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Régénérer" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Régénérer" }));
    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0]?.[0]).toEqual(REGENERATE);
  });

  it("offers to write a first synthesis", async () => {
    const user = userEvent.setup();
    loaded(EMPTY);
    renderOverview();
    expect(screen.getByText("Pas encore de synthèse")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Régénérer" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Générer" }));
    expect(mutate.mock.calls[0]?.[0]).toEqual(REGENERATE); // nothing to lose: no confirmation
    expect(screen.queryByText("Chapitres")).not.toBeInTheDocument();
    expect(screen.queryByText("Moments forts suggérés")).not.toBeInTheDocument();
  });

  it("says when a synthesis is being written", () => {
    loaded({ ...EMPTY, status: "running" });
    renderOverview();
    expect(screen.getByRole("status")).toHaveTextContent("Synthèse en cours…");
    expect(screen.queryByRole("button", { name: "Générer" })).not.toBeInTheDocument();
  });

  it("keeps the previous synthesis on screen while a new one is written", () => {
    loaded({ ...READY, status: "running" });
    renderOverview();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Nouvelle synthèse en cours : la précédente reste affichée.",
    );
    expect(screen.getByRole("heading", { name: "Le héron du lac" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Régénérer" })).toBeDisabled();
  });

  it("tells why no synthesis was written, and offers to try again", async () => {
    const user = userEvent.setup();
    loaded({ ...EMPTY, status: "skipped", note: "aucun modèle de vision chargé" });
    renderOverview();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Synthèse non générée : aucun modèle de vision chargé",
    );
    await user.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(mutate.mock.calls[0]?.[0]).toEqual(REGENERATE);
  });

  it("tells why the last run failed", () => {
    loaded({ ...EMPTY, status: "failed", note: "délai dépassé" });
    renderOverview();
    expect(screen.getByRole("status")).toHaveTextContent("La synthèse a échoué : délai dépassé");
  });

  it("keeps the last synthesis when a new run failed", () => {
    loaded({ ...READY, status: "failed", note: "délai dépassé" });
    renderOverview();
    expect(screen.getByRole("status")).toHaveTextContent(
      "La dernière génération n'a pas abouti (délai dépassé) : synthèse précédente affichée.",
    );
    expect(screen.getByRole("heading", { name: "Le héron du lac" })).toBeVisible();
  });

  it("cannot regenerate an offline video", () => {
    loaded(READY);
    renderOverview({ offline: true });
    expect(screen.getByRole("button", { name: "Régénérer" })).toBeDisabled();
  });

  it("shows placeholders while loading", () => {
    synthesis.mockReturnValue({ isPending: true, isError: false, data: undefined });
    renderOverview();
    expect(screen.queryByText("Synthèse")).not.toBeInTheDocument();
    expect(document.querySelectorAll("[data-slot=skeleton]").length).toBeGreaterThan(0);
  });

  it("shows why the synthesis could not be read", () => {
    synthesis.mockReturnValue({ isPending: false, isError: true, error: new Error("HTTP 500") });
    renderOverview();
    expect(screen.getByText("HTTP 500")).toBeVisible();
  });
});

describe("ChapterList", () => {
  it("lists the chapters with their times, and plays one from its start", async () => {
    const user = userEvent.setup();
    loaded(READY);
    const { seek } = renderOverview();
    const second = screen.getByRole("button", { name: /Chapitre 2 Envol/ });
    expect(second).toHaveTextContent("00:06 – 00:10");
    expect(second).toHaveTextContent("Le héron s'envole au-dessus du lac.");
    // The chapter at the playhead is the current one.
    expect(screen.getByRole("button", { current: true })).toHaveTextContent("Arrivée du héron");
    await user.click(second);
    expect(seek).toHaveBeenCalledWith(6);
    expect(screen.getByRole("button", { current: true })).toHaveTextContent("Envol");
  });
});

describe("HighlightList", () => {
  it("gives each highlight its cut points, the J/L-cut sound and why it was picked", () => {
    loaded(READY);
    renderOverview();
    const list = screen.getByRole("list", { name: "Moments forts suggérés" });
    const [first, second] = [...list.children] as HTMLElement[];
    if (!first || !second) throw new Error("two highlights expected");
    expect(first).toHaveTextContent("Suggestion 1");
    expect(first).toHaveTextContent("chapitre 1 · Arrivée du héron");
    expect(first).toHaveTextContent("Le héron se pose en déployant ses ailes.");
    expect(first).toHaveTextContent("Image00:02.000 → 00:04.000");
    expect(first).toHaveTextContent("Son00:01.500 → 00:04.800");
    expect(within(first).getByText("le son commence 0,5 s avant l'image (J-cut)")).toBeVisible();
    expect(within(first).getByText("le son continue 0,8 s après la coupe (L-cut)")).toBeVisible();
    const criteria = within(first).getByRole("list", { name: "Critères (indicatif)" });
    expect(
      within(criteria)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["utilisable 96", "plan fort", "parole"]);
    // The sound follows the picture: no sound range, the note still says where the cut moved.
    expect(second).not.toHaveTextContent("Son");
    expect(second).toHaveTextContent("entrée déplacée entre deux mots");
    expect(screen.getByText(/suggestions indicatives/)).toBeVisible();
  });

  it("plays a highlight from its picture's in point", async () => {
    const user = userEvent.setup();
    loaded(READY);
    const { seek } = renderOverview();
    await user.click(screen.getByRole("button", { name: "Aller au moment fort 1 (00:02)" }));
    expect(seek).toHaveBeenCalledWith(2);
    await user.click(screen.getByRole("button", { name: "Aller au moment fort 2 (00:07)" }));
    expect(seek).toHaveBeenLastCalledWith(7);
  });
});
