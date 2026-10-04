import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";

import { ApiError, type Schemas } from "@/api/client";

import { AddTimelineDialog } from "./AddTimelineDialog";
import { resetSelection, selectVideos, useTimelineBuildOpen } from "./selection";
import { PREVIEW, PROJECT, resolveTimeline, timelineBin } from "./timelineFixtures";

const navigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }));

interface Read<T> {
  data?: T;
  isError: boolean;
  error: unknown;
  isFetching: boolean;
  refetch: () => Promise<unknown>;
}
const reading = <T,>(data?: T, error?: unknown): Read<T> => ({
  data,
  isError: error !== undefined,
  error: error ?? null,
  isFetching: false,
  refetch: vi.fn(() => Promise.resolve()),
});
let project: Read<Schemas["ResolveProjectOut"]>;
let preview: Read<Schemas["TimelinePreviewOut"]>;
const previewed: (string | undefined)[][] = []; // the timelines read in Resolve, in order
const importTimeline = vi.fn();
vi.mock("@/api/queries", () => ({
  useResolveProject: () => project,
  useTimelinePreview: (projectId: string | undefined, timelineId: string | undefined) => {
    previewed.push([projectId, timelineId]);
    return preview;
  },
  useImportTimeline: () => ({ mutateAsync: importTimeline, isPending: false }),
  useTimelineBins: () => ({
    data: [timelineBin({ id: "b2", label: "Matin", auto_analyze: false })],
  }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const openDialog = async (user: ReturnType<typeof userEvent.setup>) => {
  render(<AddTimelineDialog />);
  await user.click(screen.getByRole("button", { name: "Importer depuis Resolve" }));
  return within(screen.getByRole("dialog"));
};

function BuildDialogState() {
  return <p>{useTimelineBuildOpen() ? "création ouverte" : "création fermée"}</p>;
}

beforeEach(() => {
  resetSelection();
  navigate.mockReset();
  importTimeline.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
  previewed.length = 0;
  project = reading(PROJECT);
  preview = reading(PREVIEW);
});

describe("AddTimelineDialog", () => {
  it("points to « Créer une timeline » when videos are ticked: it goes the other way", async () => {
    const user = userEvent.setup();
    selectVideos(["a", "b"], true);
    render(<BuildDialogState />);
    const dialog = await openDialog(user);
    expect(
      dialog.getByText(/^Vous avez coché 2 vidéos\. Cette fenêtre fait l'inverse/),
    ).toBeVisible();
    await user.click(
      dialog.getByRole("button", { name: "Créer une timeline avec les 2 vidéos cochées" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("création ouverte")).toBeInTheDocument();
  });

  it("lists the open project's timelines, the current one chosen, and what adding it does", async () => {
    const user = userEvent.setup();
    const dialog = await openDialog(user);
    expect(dialog.queryByText(/Vous avez coché/)).not.toBeInTheDocument(); // nothing ticked
    expect(dialog.getByText("Projet « cats 2026 »")).toBeVisible();
    expect(dialog.getByText(/Local Database/)).toBeVisible();

    const list = dialog.getByRole("radiogroup", { name: "Timelines du projet" });
    const current = within(list).getByRole("radio", { name: /^Timeline 1/ });
    expect(current).toBeChecked();
    expect(current.closest("label")).toHaveTextContent(
      "Timeline 1actuelle29,97 i/s · 03:05 · 98 plans",
    );
    expect(previewed.at(-1)).toEqual(["p1", "t1"]);

    // Only the parts with a count, then what is left out.
    expect(dialog.getByText(/déjà dans la bibliothèque ·/).closest("p")).toHaveTextContent(
      "98 vidéos : 90 déjà dans la bibliothèque · 6 à ajouter seules (dossiers non déclarés : " +
        "seules ces vidéos seront ajoutées) · 2 introuvables",
    );
    expect(dialog.queryByText(/dans vos dossiers/)).not.toBeInTheDocument();
    expect(dialog.getByText("D:\\cats 2026\\hdr")).toBeVisible();
    for (const line of [
      "2 titres ou générateurs",
      "3 clips composés ou multicam : leurs vidéos ne sont pas lues",
      "1 vidéo dans un format non pris en charge (.braw…)",
      "2 fichiers audio ou images",
    ]) {
      expect(dialog.getByText(line)).toBeVisible();
    }
    expect(dialog.getByText("2 titres ou générateurs")).toHaveAttribute(
      "title",
      "Texte+, Fond uni", // their names on hover
    );

    const analyses = "8 analyses seront demandées (seul ce qui manque)";
    expect(dialog.getByText(analyses)).toBeVisible();
    await user.click(dialog.getByRole("switch", { name: "Analyser automatiquement" }));
    expect(dialog.queryByText(analyses)).not.toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Importer la timeline" })).toBeEnabled();
  });

  it("adds the chosen timeline under the name given, then shows its videos", async () => {
    const user = userEvent.setup();
    importTimeline.mockResolvedValue({ created: true, bin: { id: "b9", label: "Chats" } });
    const dialog = await openDialog(user);
    const label = dialog.getByLabelText("Nom affiché");
    expect(label).toHaveAttribute("placeholder", "Timeline 1");
    await user.type(label, "Chats");
    await user.click(dialog.getByRole("switch", { name: "Analyser automatiquement" }));
    await user.click(dialog.getByRole("button", { name: "Importer la timeline" }));

    expect(importTimeline).toHaveBeenCalledWith({
      project_id: "p1",
      timeline_id: "t1",
      snapshot_id: "s1", // the preview's read, not a second one
      label: "Chats",
      auto_analyze: false,
    });
    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({ to: "/library", search: { timeline: "b9" } });
    });
    expect(toast.success).toHaveBeenCalledWith(
      "Timeline « Chats » importée : ses vidéos arrivent dans la bibliothèque.",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("updates a timeline already in the library", async () => {
    const user = userEvent.setup();
    preview = reading({ ...PREVIEW, to_analyze: 0 });
    const dialog = await openDialog(user);
    expect(dialog.getByText("Aucune analyse à demander : tout est déjà analysé.")).toBeVisible();
    const other = dialog.getByRole("radio", { name: /^Rushs du matin/ });
    expect(other.closest("label")).toHaveTextContent(
      "Rushs du matindéjà dans la bibliothèquemodifiée depuis",
    );
    await user.click(other);
    expect(other).toBeChecked();
    expect(previewed.at(-1)).toEqual(["p1", "t2"]);
    expect(dialog.getByLabelText("Nom affiché")).toHaveAttribute("placeholder", "Matin");
    // Its automatic analysis stays off unless switched on here.
    expect(dialog.getByRole("switch", { name: "Analyser automatiquement" })).not.toBeChecked();
    expect(dialog.queryByText(/Aucune analyse à demander/)).not.toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Mettre à jour" })).toBeEnabled();
  });

  it("says what Resolve answered instead of the project, and asks again on demand", async () => {
    const user = userEvent.setup();
    const detail =
      "DaVinci Resolve n'est pas lancé. Ouvrez votre projet dans Resolve, puis réessayez.";
    project = reading<Schemas["ResolveProjectOut"]>(
      undefined,
      new ApiError(503, "resolve_unavailable", detail),
    );
    const dialog = await openDialog(user);
    expect(dialog.getByRole("alert")).toHaveTextContent(detail);
    expect(dialog.getByRole("button", { name: "Importer la timeline" })).toBeDisabled();
    await user.click(dialog.getByRole("button", { name: "Réessayer" }));
    expect(project.refetch).toHaveBeenCalledOnce();
  });

  it("reads the project again when another one was opened in Resolve meanwhile", async () => {
    const user = userEvent.setup();
    const detail = "Le projet ouvert dans DaVinci Resolve a changé (« Vacances »).";
    preview = reading<Schemas["TimelinePreviewOut"]>(
      undefined,
      new ApiError(409, "conflict", detail),
    );
    const dialog = await openDialog(user);
    expect(dialog.getByRole("alert")).toHaveTextContent(detail);
    expect(project.refetch).toHaveBeenCalledOnce();

    preview = reading(PREVIEW);
    importTimeline.mockRejectedValue(new ApiError(409, "conflict", detail));
    await user.click(dialog.getByRole("button", { name: "Importer la timeline" }));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(detail);
    });
    expect(project.refetch).toHaveBeenCalledTimes(2);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("says when the project has no timeline", async () => {
    const user = userEvent.setup();
    project = reading({ ...PROJECT, current_timeline_id: null, timelines: [] });
    const dialog = await openDialog(user);
    expect(dialog.getByText("Ce projet n'a aucune timeline.")).toBeVisible();
    expect(dialog.getByRole("button", { name: "Importer la timeline" })).toBeDisabled();
  });

  it("filters a long list by name, and warns about a project never saved", async () => {
    const user = userEvent.setup();
    const names = ["Plage", "Forêt", "Ville", "Nuit", "Plage 2", "Port", "Marché", "Gare", "Toit"];
    project = reading({
      ...PROJECT,
      project: { id: "p2", name: "Untitled Project" },
      timelines: names.map((name, i) => resolveTimeline(`t${String(i)}`, name)),
    });
    const dialog = await openDialog(user);
    expect(dialog.getByText(/enregistrez-le d'abord/)).toBeVisible();
    await user.type(dialog.getByRole("searchbox", { name: "Filtrer les timelines" }), "plage");
    expect(
      dialog.getAllByRole("radio").map((radio) => radio.closest("label")?.textContent),
    ).toEqual([expect.stringMatching(/^Plage25 i\/s/), expect.stringMatching(/^Plage 2/)]);
    await user.clear(dialog.getByRole("searchbox", { name: "Filtrer les timelines" }));
    await user.type(dialog.getByRole("searchbox", { name: "Filtrer les timelines" }), "zzz");
    expect(dialog.getByText("Aucune timeline ne correspond.")).toBeVisible();
  });
});
