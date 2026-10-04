import { act, render, screen } from "@testing-library/react";

import i18n from "@/i18n";

import { SlowResolve } from "./ResolveNotes";
import { PREVIEW, SKIPPED, timelineBin } from "./timelineFixtures";
import { previewParts, sameLabels, skippedLines, stateParts } from "./timelines";

const fr = i18n.getFixedT("fr");
const en = i18n.getFixedT("en");

describe("previewParts", () => {
  it("says what adding the timeline would do, with the parts that have a count only", () => {
    expect(previewParts(fr, PREVIEW)).toEqual([
      "90 déjà dans la bibliothèque",
      "6 à ajouter seules (dossiers non déclarés : seules ces vidéos seront ajoutées)",
      "2 introuvables",
    ]);
    expect(previewParts(en, { ...PREVIEW, new_folder: 0, missing: 1, unknown: 3 })).toEqual([
      "90 already in the library",
      "1 not found",
      "3 on an unreachable network share",
    ]);
  });
});

describe("skippedLines", () => {
  it("gives a line per kind left out, with the names Resolve gave", () => {
    const lines = skippedLines(fr, SKIPPED, 1);
    expect(lines.map((line) => line.text)).toEqual([
      "2 titres ou générateurs",
      "3 clips composés ou multicam : leurs vidéos ne sont pas lues",
      "1 vidéo dans un format non pris en charge (.braw…)",
      "2 fichiers audio ou images",
      "1 élément que Resolve n'a pas pu décrire",
    ]);
    expect(lines[0]?.names).toEqual(["Texte+", "Fond uni"]);
  });
});

describe("stateParts", () => {
  it("counts the files in the library, then those calling for a look", () => {
    const bin = timelineBin({
      states: {
        in_library: 90,
        adding: 0,
        not_processed: 0,
        removed: 1,
        missing: 2,
        outside: 1,
        error: 1,
      },
    });
    expect(stateParts(fr, bin)).toEqual([
      "90 vidéos dans la bibliothèque",
      "1 retirée de la bibliothèque",
      "2 introuvables",
      "1 hors bibliothèque",
      "1 en erreur",
    ]);
  });

  it("follows an update: waiting, then the files added so far", () => {
    const job = { id: "j1", status: "queued" as const, progress: 0, message: null };
    expect(stateParts(fr, timelineBin({ sync_job: job }))).toContain("mise à jour en attente");
    const running = timelineBin({ sync_job: { ...job, status: "running", progress: 0.1225 } });
    expect(stateParts(fr, running)).toContain("ajout en cours (12/98)");
    const pending = timelineBin({
      sync_job: { ...job, status: "failed" },
      states: { ...running.states, not_processed: 3 },
    });
    expect(stateParts(fr, pending)).toContain("3 non traitées — relancez la mise à jour");
  });
});

describe("sameLabels", () => {
  it("finds the names two timelines share, whatever their case", () => {
    const bins = [
      timelineBin({ id: "a", label: "Timeline 1" }),
      timelineBin({ id: "b", label: "timeline 1" }),
      timelineBin({ id: "c", label: "Montage" }),
    ];
    expect([...sameLabels(bins)]).toEqual(["timeline 1"]);
  });
});

describe("SlowResolve", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("says Resolve is slow once a read has lasted 3 s, and starts over for the next one", () => {
    const slow = "DaVinci Resolve met du temps à répondre…";
    const { rerender } = render(<SlowResolve pending />);
    act(() => {
      vi.advanceTimersByTime(2999);
    });
    expect(screen.queryByText(slow)).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(screen.getByText(slow)).toBeInTheDocument();

    rerender(<SlowResolve pending={false} />);
    expect(screen.queryByText(slow)).not.toBeInTheDocument();
    rerender(<SlowResolve pending />); // the next read: 3 s again
    expect(screen.queryByText(slow)).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByText(slow)).toBeInTheDocument();
  });
});
