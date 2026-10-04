import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { VisionCard } from "./VisionCard";

const mutate = vi.fn();
let status: unknown;
vi.mock("@/api/queries", () => ({
  useVisionProfile: () => ({ data: status, isPending: false }),
  useProbeVision: () => ({ mutate, isPending: false }),
}));

const profile = {
  probed_at: "2026-09-27T14:02:00Z",
  wall_ms: 9732,
  truncated: false,
  reasoning_tokens_seen: 0,
  grounding: {
    enabled: true,
    convention: "yxyx_1000",
    box_field: "box_2d",
    mean_iou: 0.9,
    precise: true,
    reason: null,
  },
};

beforeEach(() => {
  mutate.mockReset();
});

describe("VisionCard", () => {
  it("shows what the calibration measured, and recalibrates on demand", async () => {
    const user = userEvent.setup();
    status = {
      model: "google/gemma-4-12b",
      display_name: "Gemma 4 12B",
      prior: false,
      reasoning_capable: true,
      profile,
    };
    render(<VisionCard />);
    expect(screen.getByText("complètes")).toBeVisible();
    expect(screen.getByText("coupé pour les analyses")).toBeVisible();
    expect(
      screen.getByText("vérifiées : [y1, x1, y2, x2] sur 0-1000 (box_2d), IoU 0,90"),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Recalibrer" }));
    expect(mutate).toHaveBeenCalledOnce();
  });

  it("gives the measured scale, and a cut answer does not contradict the positions", () => {
    status = {
      model: "qwen/qwen2.5-vl-7b",
      display_name: "Qwen2.5-VL 7B",
      prior: false,
      reasoning_capable: false,
      profile: {
        ...profile,
        truncated: true,
        grounding: { ...profile.grounding, convention: "xyxy_px", box_field: "bbox_2d" },
      },
    };
    render(<VisionCard />);
    expect(
      screen.getByText("vérifiées : [x1, y1, x2, y2] en pixels (bbox_2d), IoU 0,90"),
    ).toBeVisible();
    expect(screen.getByText("tronquées au moins une fois (boucle ou raisonnement)")).toBeVisible();
    expect(screen.queryByText(/positions désactivées/)).not.toBeInTheDocument();
  });

  it("says when Qwen3-VL relies on its known convention", () => {
    status = { model: "qwen/qwen3-vl-4b", prior: true, reasoning_capable: false, profile: null };
    render(<VisionCard />);
    expect(screen.getByText("convention connue (Qwen3-VL), non recalibrée")).toBeVisible();
  });

  it("cannot recalibrate without a loaded model", () => {
    status = { model: null, prior: false, reasoning_capable: false, profile: null };
    render(<VisionCard />);
    expect(screen.getByRole("button", { name: "Recalibrer" })).toBeDisabled();
    expect(screen.getByText("Aucun modèle de vision chargé dans LM Studio.")).toBeVisible();
  });

  it("tells LM Studio is unreachable rather than asking for a model", () => {
    status = { model: null, lmstudio_error: "LM Studio injoignable", profile: null };
    render(<VisionCard />);
    expect(screen.getByText(/LM Studio est injoignable/)).toBeVisible();
  });
});
