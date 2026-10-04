import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Keyframe } from "@/api/client";

import { FrameDetails } from "./FrameDetails";
import type { Subject } from "./subjectBoxes";

const FRAME: Keyframe = {
  id: "kf0",
  idx: 0,
  t_s: 2,
  width: 1280,
  height: 720,
  selection_reason: "scene",
  sharpness: 10,
  shot_id: null,
  metrics: null,
  image_url: "/img/0.jpg",
  thumb_url: "/thumb/0.jpg",
  analysis: null,
};

const CAT: Subject = {
  label: "chat",
  category: "mammal",
  box: [0.1, 0.2, 0.4, 0.8],
  score: 0.93,
  main: true,
  sources: ["vlm", "detector"],
  face_box: null,
  face_points: null,
};

const PERSON: Subject = {
  label: "personne",
  category: "person",
  box: [0.5, 0.0, 0.9, 1.0],
  score: 0.9,
  main: false,
  sources: ["detector", "faces"],
  face_box: [0.6, 0.1, 0.8, 0.3],
  face_points: null,
};

const READY = { status: "ready", run_status: "ready", note: null };

describe("FrameDetails subjects", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("draws a box per subject, at its place in the image", () => {
    render(<FrameDetails frame={FRAME} subjects={[CAT, PERSON]} subjectsPart={READY} />);
    const boxes = screen.getAllByTestId("subject-box");
    expect(boxes).toHaveLength(2);
    const cat = boxes[0];
    if (!cat) throw new Error("box missing");
    expect(cat.style.left).toBe("10%");
    expect(cat.style.top).toBe("20%");
    expect(cat.style.width).toBe("30%");
    expect(cat.style.height).toBe("60%");
    expect(cat).toHaveTextContent("chat · principal");
    // The face is placed in image coordinates, not inside its person's outline.
    const face = screen.getByTestId("face-box");
    expect(face.style.left).toBe("60%");
    expect(face.style.width).toBe("20%");
    expect(screen.getByText("Positions seulement : personne n'est identifié.")).toBeInTheDocument();
  });

  it("hides the boxes on demand and remembers it", async () => {
    render(<FrameDetails frame={FRAME} subjects={[CAT]} subjectsPart={READY} />);
    // A toggle keeps its name; its state is aria-pressed.
    const toggle = screen.getByRole("button", { name: "Cadres des sujets" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(toggle);
    expect(screen.queryAllByTestId("subject-box")).toHaveLength(0);
    expect(window.localStorage.getItem("vfe.subjectBoxes")).toBe("off");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("says why there are no positions", () => {
    const skipped = {
      status: "skipped",
      run_status: "skipped",
      note: "lancez vfe models subjects",
    };
    const { rerender } = render(<FrameDetails frame={FRAME} subjectsPart={skipped} />);
    expect(screen.getByText(/lancez vfe models subjects/)).toBeInTheDocument();
    rerender(<FrameDetails frame={FRAME} subjectsPart={READY} />);
    expect(screen.getByText("Aucun être vivant repéré sur cette image.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cadres des sujets" })).not.toBeInTheDocument();
    rerender(<FrameDetails frame={FRAME} subjectsLoading />);
    expect(screen.queryByText(/lancez « Compléter »/)).not.toBeInTheDocument();
  });

  it("says when one of the two sources did not look", () => {
    const part = {
      ...READY,
      detector: { status: "ready", note: null },
      vision: { status: "skipped", note: "LM Studio injoignable" },
    };
    render(<FrameDetails frame={FRAME} subjects={[CAT]} subjectsPart={part} />);
    expect(screen.getByText("Modèle de vision : LM Studio injoignable")).toBeInTheDocument();
  });
});
