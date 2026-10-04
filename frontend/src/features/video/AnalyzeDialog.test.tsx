import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { VideoDetail } from "@/api/client";

import { AnalyzeDialog } from "./AnalyzeDialog";
import { MissingAnalyses } from "./MissingAnalyses";

const mutate = vi.fn();
vi.mock("@/api/queries", () => ({
  useAnalyze: () => ({ mutate, isPending: false }),
}));

beforeEach(() => {
  mutate.mockReset();
  mutate.mockImplementation((_body: unknown, options?: { onSuccess?: () => void }) => {
    options?.onSuccess?.(); // the request was accepted: the dialog closes
  });
});

describe("AnalyzeDialog", () => {
  it("keeps finished work unless another mode is chosen", async () => {
    const user = userEvent.setup();
    render(<AnalyzeDialog videoId="v1" missing={["weather"]} outdated={["probe"]} />);
    await user.click(screen.getByRole("button", { name: "Analyser…" }));

    expect(screen.getByText("À faire : Météo (Open-Meteo).")).toBeInTheDocument();
    expect(
      screen.getByText(/version précédente \(gardé tel quel\) : Analyse du conteneur/),
    ).toBeVisible();
    expect(screen.getByRole("radio", { name: /Compléter \(recommandé\)/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Compléter" }));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ mode: "complete", focus: null });

    await user.click(screen.getByRole("button", { name: "Analyser…" }));
    await user.click(screen.getByRole("radio", { name: /Tout refaire/ }));
    await user.type(screen.getByLabelText("Objectif d'analyse pour ce passage"), " oiseaux ");
    await user.click(screen.getByRole("button", { name: "Tout refaire" }));
    expect(mutate.mock.calls[1]?.[0]).toEqual({ mode: "full", focus: "oiseaux" });
  });
});

describe("MissingAnalyses", () => {
  const detail = (status: VideoDetail["status"], missing: string[]): VideoDetail =>
    ({ id: "v1", status, missing_stages: missing, outdated_stages: [] }) as unknown as VideoDetail;

  it("offers to complete an analysed video that lacks stages", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<MissingAnalyses detail={detail("ready", ["place", "sun"])} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Analyses à compléter pour cette vidéo : Lieu, Soleil et lumière.",
    );
    await user.click(screen.getByRole("button", { name: "Compléter" }));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ mode: "complete" });

    rerender(<MissingAnalyses detail={detail("analyzing", ["place"])} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    rerender(<MissingAnalyses detail={detail("ready", [])} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
