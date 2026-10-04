import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SelectionBar } from "./SelectionBar";
import { rangeOf, resetSelection, selectVideos, useSelectedVideos } from "./selection";

vi.mock("@/api/queries", () => ({
  useExportSidecars: () => ({ mutate: vi.fn(), isPending: false }), // see ExportButton.test
  useExportCsv: () => ({ mutate: vi.fn(), isPending: false }), // see CsvButton.test
  useBuildResolveTimeline: () => ({ mutateAsync: vi.fn(), isPending: false }), // see TimelineBuildDialog.test
}));

beforeEach(() => {
  resetSelection();
});

describe("rangeOf", () => {
  const ids = ["a", "b", "c", "d"];

  it("spans from the last card ticked to the one clicked, either way", () => {
    expect(rangeOf(ids, "b", "d")).toEqual(["b", "c", "d"]);
    expect(rangeOf(ids, "d", "a")).toEqual(["a", "b", "c", "d"]);
  });

  it("falls back to the clicked card alone", () => {
    expect(rangeOf(ids, null, "c")).toEqual(["c"]);
    expect(rangeOf(ids, "gone", "c")).toEqual(["c"]); // anchor filtered out since
  });
});

function Harness({ ids }: { ids: string[] }) {
  return <SelectionBar ids={ids} selected={useSelectedVideos()} />;
}

describe("SelectionBar", () => {
  it("ticks the videos shown and counts the ones the filters hide", async () => {
    const user = userEvent.setup();
    render(<Harness ids={["a", "b", "c"]} />);
    const all = screen.getByRole("checkbox", { name: "Sélectionner les 3 vidéos affichées" });
    expect(all).not.toBeChecked();

    act(() => {
      selectVideos(["a", "hidden"], true);
    });
    expect(all).toHaveProperty("indeterminate", true);
    expect(
      screen.getByText(/2 vidéos sélectionnées \(dont 1 masquée par les filtres\)/),
    ).toBeVisible();

    await user.click(all);
    expect(all).toBeChecked();
    expect(screen.getByText(/4 vidéos sélectionnées/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Tout désélectionner" }));
    expect(all).not.toBeChecked();
    expect(screen.queryByText(/sélectionnées/)).not.toBeInTheDocument();
  });
});
