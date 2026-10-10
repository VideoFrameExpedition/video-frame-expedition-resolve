import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError, type Schemas } from "@/api/client";

import { DataCard } from "./DataCard";

type DataState = Schemas["DataOut"];
interface Callbacks<T> {
  onSuccess?: (value: T) => void;
  onError?: (error: unknown) => void;
}

const exportData = vi.fn();
const importData = vi.fn();
const resetData = vi.fn();
const cancel = vi.fn();
const restart = vi.fn();
let state: DataState | undefined;
vi.mock("@/api/queries", () => ({
  useDataState: () => ({ data: state, isError: false }),
  useExportData: () => ({ mutate: exportData, isPending: false }),
  useImportData: () => ({ mutate: importData, isPending: false }),
  useResetData: () => ({ mutate: resetData, isPending: false }),
  useCancelPendingData: () => ({ mutate: cancel, isPending: false }),
  useRestart: () => ({ mutate: restart, isPending: false }),
  exportUrl: (token: string) => `/api/v1/system/data/export/${token}`,
}));

const get = vi.fn();
vi.mock("@/api/client", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  api: { GET: (...args: unknown[]) => get(...args) as unknown },
}));

const downloadLink = vi.fn();
vi.mock("@/lib/download", () => ({ downloadLink: (url: string) => downloadLink(url) as unknown }));

const reload = vi.fn();
vi.mock("@/lib/page", () => ({ page: { reload: () => reload() as unknown } }));

const toastError = vi.fn();
const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
  toast: {
    error: (text: string) => toastError(text) as unknown,
    success: (text: string) => toastSuccess(text) as unknown,
  },
}));

const STATE: DataState = {
  database_bytes: 83 * 2 ** 20,
  media_bytes: 2 ** 30,
  videos: 163,
  backups_dir: "C:\\Users\\me\\AppData\\Local\\vfe-vision\\backups",
  pending: null,
  last: null,
  can_restart: true,
};

function callbacks<T>(mock: ReturnType<typeof vi.fn>): Callbacks<T> {
  const call = mock.mock.calls.at(-1) as [unknown, Callbacks<T>];
  return call[1];
}

beforeEach(() => {
  for (const mock of [exportData, importData, resetData, cancel, restart, get, downloadLink]) {
    mock.mockReset();
  }
  reload.mockReset();
  toastError.mockReset();
  toastSuccess.mockReset();
  state = STATE;
});

describe("DataCard", () => {
  it("exports the library with its frames, then downloads it", async () => {
    const user = userEvent.setup();
    render(<DataCard />);
    expect(screen.getByText(/163 vidéos · base de données 83/)).toBeVisible();
    expect(screen.getByText(/backups$/)).toBeVisible();
    const images = screen.getByRole("checkbox", { name: /Avec les images extraites/ });
    expect(images).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Exporter" }));
    expect(exportData).toHaveBeenCalledWith(true, expect.anything());
    act(() => {
      callbacks<Schemas["ExportOut"]>(exportData).onSuccess?.({
        token: "a".repeat(32),
        size_bytes: 2 ** 30,
        images: true,
      });
    });
    expect(downloadLink).toHaveBeenCalledWith(`/api/v1/system/data/export/${"a".repeat(32)}`);
    expect(toastSuccess).toHaveBeenCalledWith(expect.stringMatching(/^Archive prête/));

    await user.click(images);
    await user.click(screen.getByRole("button", { name: "Exporter" }));
    expect(exportData).toHaveBeenLastCalledWith(false, expect.anything());
  });

  it("imports a file once it is confirmed, keeping this computer's settings", async () => {
    const user = userEvent.setup();
    render(<DataCard />);
    const start = screen.getByRole("button", { name: "Importer…" });
    expect(start).toBeDisabled();
    const file = new File(["PK"], "bibliotheque.zip", { type: "application/zip" });
    await user.upload(screen.getByLabelText("Fichier à importer"), file);
    expect(screen.getByRole("checkbox", { name: /Garder les réglages/ })).toBeChecked();
    await user.click(start);
    expect(screen.getByRole("dialog")).toHaveTextContent("« bibliotheque.zip »");
    expect(importData).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Importer" }));
    expect(importData).toHaveBeenCalledWith({ file, keepSettings: true }, expect.anything());
    act(() => {
      callbacks(importData).onSuccess?.(undefined);
    });
    expect(toastSuccess).toHaveBeenCalledWith(
      "Import prêt : redémarrez l'application pour l'appliquer.",
    );

    // A file of a newer version: said in the interface's language.
    act(() => {
      callbacks(importData).onError?.(new ApiError(422, "newer_library", "plus récente"));
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/mettez d'abord celle-ci à jour/),
    );
  });

  it("resets what is ticked, once confirmed", async () => {
    const user = userEvent.setup();
    render(<DataCard />);
    const library = screen.getByRole("checkbox", { name: /La bibliothèque/ });
    const settings = screen.getByRole("checkbox", { name: /Les réglages/ });
    expect(library).toBeChecked();
    expect(settings).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Réinitialiser…" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(/la bibliothèque sera effacée/);
    expect(screen.getByRole("dialog")).toHaveTextContent(/exportez d'abord/);
    await user.click(screen.getByRole("button", { name: "Réinitialiser" }));
    expect(resetData).toHaveBeenCalledWith({ library: true, settings: false }, expect.anything());
    act(() => {
      callbacks(resetData).onSuccess?.(undefined);
    });
    expect(toastSuccess).toHaveBeenCalledWith(
      "Réinitialisation prête : redémarrez l'application pour l'appliquer.",
    );

    await user.click(library);
    expect(screen.getByRole("button", { name: "Réinitialiser…" })).toBeDisabled();
    await user.click(settings);
    await user.click(screen.getByRole("button", { name: "Réinitialiser…" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(/installation neuve/);
  });

  it("asks for a restart under the import, then reloads the page", async () => {
    state = {
      ...STATE,
      pending: {
        action: "import",
        library: true,
        settings: false,
        images: true,
        videos: 163,
        source: "bibliotheque.zip",
        prepared_at: "2026-10-09T16:00:00Z",
      },
    };
    const user = userEvent.setup();
    render(<DataCard />);
    // Under the import, where the eyes are: what is ready, and the button that applies it.
    const notice = within(
      within(screen.getByRole("region", { name: "Importer" })).getByRole("alert"),
    );
    expect(
      notice.getByText("Import prêt : redémarrez l'application pour l'appliquer."),
    ).toBeVisible();
    expect(
      notice.getByText(
        /« bibliotheque.zip », 163 vidéos\. Avec ses images extraites\. Les réglages/,
      ),
    ).toBeVisible();
    expect(notice.getByText(/sans ouvrir d'autre onglet/)).toBeVisible();
    expect(
      within(screen.getByRole("region", { name: "Réinitialiser" })).queryByRole("alert"),
    ).not.toBeInTheDocument();
    await user.click(notice.getByRole("button", { name: "Annuler" }));
    expect(cancel).toHaveBeenCalled();
    await user.click(notice.getByRole("button", { name: "Redémarrer maintenant" }));
    expect(restart).toHaveBeenCalled();

    vi.useFakeTimers();
    try {
      get.mockResolvedValueOnce({ data: state }); // still the old start
      get.mockRejectedValueOnce(new TypeError("Failed to fetch")); // stopped
      get.mockResolvedValue({ data: { ...STATE, pending: null } }); // the new start
      act(() => {
        callbacks(restart).onSuccess?.(undefined);
      });
      expect(screen.getByText(/La page se rechargera/)).toBeVisible();
      for (let tick = 0; tick < 3; tick += 1) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(1500);
        });
      }
      expect(reload).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says how to apply it when the application cannot restart by itself", () => {
    state = {
      ...STATE,
      can_restart: false,
      pending: {
        action: "reset",
        library: true,
        settings: true,
        images: false,
        prepared_at: "2026-10-09T16:00:00Z",
      },
    };
    render(<DataCard />);
    const notice = within(
      within(screen.getByRole("region", { name: "Réinitialiser" })).getByRole("alert"),
    );
    expect(notice.getByText(/La bibliothèque et les réglages seront effacés/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Redémarrer maintenant" })).not.toBeInTheDocument();
    expect(notice.getByText(/fermez sa fenêtre ou appuyez sur Ctrl\+C/)).toBeVisible();
    expect(notice.queryByText(/sans ouvrir d'autre onglet/)).not.toBeInTheDocument();
  });

  it("tells under the import what the last start did, and where the previous database went", () => {
    const last: Schemas["LastOperationOut"] = {
      action: "import",
      library: true,
      settings: false,
      images: true,
      done_at: "2026-10-09T16:05:00Z",
      backup: "vfe-20261009T160500Z-0018-before-import.sqlite3",
    };
    state = { ...STATE, last };
    const { rerender } = render(<DataCard />);
    const imports = within(screen.getByRole("region", { name: "Importer" }));
    expect(imports.getByText(/Import fait le .* dans les sauvegardes : vfe-2026/)).toBeVisible();
    state = { ...STATE, last: { ...last, error: "Accès refusé" } };
    rerender(<DataCard />);
    expect(screen.getByText(/a échoué : Accès refusé\. Vos données sont restées/)).toBeVisible();
  });
});
