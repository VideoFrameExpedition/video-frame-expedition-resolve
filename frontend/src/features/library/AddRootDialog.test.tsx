import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { AddRootDialog } from "./AddRootDialog";

interface Picked {
  path: string | null;
}
let answer: (picked: Picked) => void = () => undefined;
// POST /system/pick-folder: pending while the Windows folder dialog is open.
const pickFolder = vi.fn(
  () =>
    new Promise<Picked>((resolve) => {
      answer = resolve;
    }),
);
// GET /system/lmstudio/models: a vision model loaded, unless a test says otherwise.
const VISION = { vision: true, loaded_instances: [{ id: "qwen/qwen3-vl-8b" }] };
let models: { isError: boolean; isSuccess: boolean; data?: (typeof VISION)[] } = {
  isError: false,
  isSuccess: true,
  data: [VISION],
};
vi.mock("@/api/queries", async () => {
  const { useMutation } = await import("@tanstack/react-query");
  return {
    useAddRoot: () => ({ mutate: vi.fn(), isPending: false }),
    useLmModels: () => models,
    usePickFolder: () => useMutation({ mutationFn: pickFolder }), // the real one-at-a-time rules
  };
});
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...rest }: { children: ReactNode; to: string }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const renderDialog = (): void => {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AddRootDialog />
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  pickFolder.mockClear();
  vi.mocked(toast.error).mockReset();
  models = { isError: false, isSuccess: true, data: [VISION] };
});

describe("AddRootDialog", () => {
  it("opens the Windows folder dialog first, and fills in the folder chosen", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Ajouter un dossier" }));
    expect(pickFolder).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Fenêtre ouverte…" })).toBeDisabled();

    answer({ path: "D:\\Rushs\\Plage" });
    expect(await screen.findByDisplayValue("D:\\Rushs\\Plage")).toBe(
      screen.getByLabelText("Chemin du dossier"),
    );
    expect(screen.getByRole("button", { name: "Parcourir…" })).toBeEnabled();
  });

  it("keeps the one folder dialog open when the form is closed and reopened", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Ajouter un dossier" }));
    await user.click(screen.getByRole("button", { name: "Annuler" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Ajouter un dossier" }));
    expect(pickFolder).toHaveBeenCalledOnce(); // no second request (the server would refuse it)
    expect(screen.getByRole("button", { name: "Fenêtre ouverte…" })).toBeDisabled();

    answer({ path: "E:\\Tournage" }); // the folder chosen in the first dialog
    expect(await screen.findByDisplayValue("E:\\Tournage")).toBe(
      screen.getByLabelText("Chemin du dossier"),
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("says nothing of LM Studio when a vision model is loaded", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Ajouter un dossier" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("warns when LM Studio does not answer, and points to where it is set", async () => {
    models = { isError: true, isSuccess: false };
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Ajouter un dossier" }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("LM Studio ne répond pas");
    // Read before the folder dialog, which would come over it: opened with « Parcourir… ».
    expect(pickFolder).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Parcourir…" }));
    expect(pickFolder).toHaveBeenCalledOnce();
    expect(alert).toHaveTextContent("complétées toutes seules");
    expect(
      screen.getByRole("link", { name: "page Système, carte « Serveur de modèles »" }),
    ).toHaveAttribute("href", "/system");
  });

  it("warns too when LM Studio answers without a vision model loaded", async () => {
    models = { isError: false, isSuccess: true, data: [{ vision: true, loaded_instances: [] }] };
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Ajouter un dossier" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Aucun modèle de vision chargé dans LM Studio",
    );
  });
});
