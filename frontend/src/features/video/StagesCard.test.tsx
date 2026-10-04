import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { VideoDetail } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";

import { StagesCard } from "./VideoFacts";

const mutate = vi.fn();
const stage = (name: string, requires: string[], resource = "cpu") => ({
  name,
  requires,
  resource,
  family: "image",
  after: [],
  optional: false,
});
const CATALOGUE = [
  stage("place", []),
  stage("proxy", []),
  stage("keyframes", []),
  stage("vision_frames", ["keyframes"], "lmstudio"),
  stage("ocr", ["keyframes"]),
];
let catalogue: typeof CATALOGUE | undefined = CATALOGUE;
vi.mock("@/api/queries", () => ({
  useAnalyze: () => ({ mutate, isPending: false }),
  useStages: () => ({ data: catalogue }),
}));

const run = (
  stage: string,
  status: string,
  skip_reason: string | null = null,
  summary: Record<string, unknown> = {},
) => ({ stage, status, duration_ms: 1200, error: null, skip_reason, summary });

const detail = (status: VideoDetail["status"]): VideoDetail =>
  ({
    id: "v1",
    status,
    stages: [
      run("place", "succeeded"),
      run("proxy", "skipped", "Lisible directement par le navigateur"),
      run("keyframes", "succeeded"),
      run("vision_frames", "succeeded"),
      run("ocr", "running"),
    ],
  }) as unknown as VideoDetail;

beforeEach(() => {
  mutate.mockReset();
  catalogue = CATALOGUE;
});

const renderCard = (video: VideoDetail) =>
  render(
    <TooltipProvider>
      <StagesCard video={video} />
    </TooltipProvider>,
  );

describe("StagesCard", () => {
  it("runs one stage again", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <StagesCard video={detail("ready")} />
      </TooltipProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Relancer « Lieu »" }));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ stages: ["place"], mode: "full" });

    // A skipped stage can be tried again; one already running cannot.
    expect(screen.getByRole("button", { name: "Relancer « Copie de visionnage »" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Relancer « Texte à l'écran »" })).toBeDisabled();
  });

  it("asks first when other stages need the result", async () => {
    const user = userEvent.setup();
    render(
      <TooltipProvider>
        <StagesCard video={detail("ready")} />
      </TooltipProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Relancer « Images clés »" }));
    expect(mutate).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "Relancer « Images clés » ?" });
    // The descriptions have a result: redone. OCR never finished here: left alone.
    expect(within(dialog).getByRole("listitem")).toHaveTextContent("Description des images");
    expect(within(dialog).getByText(/modèle de vision ou la transcription/)).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Relancer" }));
    expect(mutate.mock.calls[0]?.[0]).toEqual({ stages: ["keyframes"], mode: "full" });
  });

  it("cannot run anything on a file out of reach", () => {
    render(
      <TooltipProvider>
        <StagesCard video={detail("offline")} />
      </TooltipProvider>,
    );
    for (const button of screen.getAllByRole("button", { name: /^Relancer/ })) {
      expect(button).toBeDisabled();
    }
  });
});

describe("StagesCard confirmation", () => {
  const withRuns = (...stages: ReturnType<typeof run>[]): VideoDetail =>
    ({ id: "v1", status: "ready", stages }) as unknown as VideoDetail;

  it("lists only what the engine will redo, and gives the focus back", async () => {
    const user = userEvent.setup();
    renderCard(
      withRuns(
        run("keyframes", "succeeded"),
        // LM Studio was down: no result, so not redone with the keyframes.
        run("vision_frames", "skipped", "LM Studio injoignable", { retryable: true }),
        // Waiting for the keyframes: redone with them.
        run("ocr", "skipped", "Dépend de : keyframes", {
          retryable: true,
          blocked_by: ["keyframes"],
        }),
      ),
    );
    const rerun = screen.getByRole("button", { name: "Relancer « Images clés »" });
    await user.click(rerun);
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual(["Texte à l'écran"]);
    expect(within(dialog).queryByText(/modèle de vision/)).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));
    expect(mutate).not.toHaveBeenCalled();
    expect(rerun).toHaveFocus();
  });

  it("does not ask when nothing else has a result", async () => {
    const user = userEvent.setup();
    renderCard(
      withRuns(
        run("keyframes", "succeeded"),
        run("vision_frames", "skipped", "LM Studio injoignable", { retryable: true }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Relancer « Images clés »" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mutate.mock.calls[0]?.[0]).toEqual({ stages: ["keyframes"], mode: "full" });
  });

  it("waits for the stage catalogue before offering a rerun", () => {
    catalogue = undefined;
    renderCard(withRuns(run("keyframes", "succeeded"), run("vision_frames", "succeeded")));
    expect(screen.getByRole("button", { name: "Relancer « Images clés »" })).toBeDisabled();
  });
});
