import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/api/client";

import { LmStudioCard } from "./LmStudioCard";
import { names } from "./lmStudioAddress";

type Link = Schemas["LmStudioLinkOut"];

const choose = vi.fn();
const test = vi.fn();
const forget = vi.fn();
let link: Link | undefined;
let tried: Schemas["LmStudioTestOut"] | undefined;
let answers = true;
vi.mock("@/api/queries", () => ({
  useLmStudioLink: () => ({ data: link }),
  useLmModels: () => ({ isPending: false, isError: !answers }),
  useChooseLmStudio: () => ({ mutate: choose, isPending: false }),
  useTestLmStudio: () => ({
    mutate: test,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    data: tried,
  }),
  useForgetLmStudio: () => ({ mutate: forget, isPending: false }),
}));

const HERE: Link = {
  url: "http://127.0.0.1:1234",
  custom: false,
  default_url: "http://127.0.0.1:1234",
  local: true,
  has_token: false,
  past: [],
};
const OTHER = "http://192.168.1.20:1234";
const past = (url: string, has_token = false): Link["past"][number] => ({
  url,
  last_used_at: "2026-10-01T18:30:00Z",
  local: false,
  has_token,
});

beforeEach(() => {
  choose.mockReset();
  test.mockReset();
  forget.mockReset();
  link = HERE;
  tried = undefined;
  answers = true;
});

describe("LmStudioCard", () => {
  it("uses the LM Studio of this computer by default", () => {
    render(<LmStudioCard />);
    expect(screen.getByText("http://127.0.0.1:1234")).toBeVisible();
    expect(screen.getByText("cet ordinateur")).toBeVisible();
    expect(screen.getByText("répond")).toBeVisible();
    expect(
      screen.getByRole("radio", { name: "Sur cet ordinateur (http://127.0.0.1:1234)" }),
    ).toBeChecked();
    expect(screen.queryByLabelText("Adresse de l'autre ordinateur")).not.toBeInTheDocument();
    expect(screen.getByText(/Aucune pour l'instant/)).toBeVisible();
  });

  it("tries another computer, then talks to it", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<LmStudioCard />);
    await user.click(screen.getByRole("radio", { name: "Sur un autre ordinateur" }));
    // The frames leave this computer: said before anything is saved.
    expect(screen.getByText(/seront envoyées à cet ordinateur/)).toBeVisible();
    const test_ = screen.getByRole("button", { name: "Tester" });
    expect(test_).toBeDisabled();
    const address = screen.getByLabelText("Adresse de l'autre ordinateur");
    expect(address).toHaveAccessibleDescription(/Serve on Local Network/);
    await user.type(address, " 192.168.1.20 ");
    await user.click(test_);
    expect(test).toHaveBeenCalledWith({ address: "192.168.1.20" });

    tried = {
      url: OTHER,
      ok: true,
      local: false,
      models: 12,
      vision_models: 5,
      loaded: ["Qwen3 VL 8B"],
    };
    rerender(<LmStudioCard />);
    expect(
      screen.getByText(/LM Studio répond sur http:\/\/192\.168\.1\.20:1234 : 12 modèles, dont 5/),
    ).toHaveTextContent("Chargé : Qwen3 VL 8B.");

    await user.type(screen.getByLabelText("Jeton d'API (facultatif)"), "secret");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(choose).toHaveBeenCalledWith(
      { address: "192.168.1.20", token: "secret" },
      expect.anything(),
    );
  });

  it("says why an address does not answer", () => {
    link = { ...HERE, url: OTHER, custom: true, local: false, past: [past(OTHER)] };
    answers = false;
    tried = {
      url: OTHER,
      ok: false,
      local: false,
      models: 0,
      vision_models: 0,
      error: "LM Studio ne répond pas sur…",
    };
    render(<LmStudioCard />);
    expect(screen.getByText("un autre ordinateur")).toBeVisible();
    expect(screen.getByText("ne répond pas")).toBeVisible();
    expect(screen.getByText("LM Studio ne répond pas sur…")).toBeVisible();
    expect(screen.getByLabelText("Adresse de l'autre ordinateur")).toHaveValue(OTHER);
  });

  it("keeps the past connections one click away", async () => {
    const user = userEvent.setup();
    link = {
      ...HERE,
      url: OTHER,
      custom: true,
      local: false,
      has_token: true,
      past: [past(OTHER, true), past("http://pc-salon:5000")],
    };
    render(<LmStudioCard />);
    const list = within(screen.getByRole("region", { name: "Connexions passées" }));
    const [current, older] = list.getAllByRole("listitem") as [HTMLElement, HTMLElement];
    // The one in use is neither chosen again nor forgotten.
    expect(current).toHaveTextContent("utilisée en ce moment");
    expect(current).toHaveTextContent("jeton enregistré");
    expect(within(current).queryByRole("button")).not.toBeInTheDocument();
    expect(older).toHaveTextContent(/utilisée le 1 oct\. 2026/);
    // Its token is kept unless another one is typed.
    expect(screen.getByLabelText("Jeton d'API (facultatif)")).toHaveAccessibleDescription(
      /laissez vide pour le garder/,
    );

    await user.click(list.getByRole("button", { name: "Utiliser http://pc-salon:5000" }));
    expect(choose).toHaveBeenCalledWith({ address: "http://pc-salon:5000" }, expect.anything());
    await user.click(list.getByRole("button", { name: "Oublier http://pc-salon:5000" }));
    expect(forget).toHaveBeenCalledWith("http://pc-salon:5000", expect.anything());

    // Back to this computer: no address at all.
    await user.click(screen.getByRole("radio", { name: /Sur cet ordinateur/ }));
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(choose).toHaveBeenLastCalledWith({ address: null }, expect.anything());
  });

  it("waits for the address", () => {
    link = undefined;
    render(<LmStudioCard />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});

describe("names", () => {
  it("recognises a remembered address in what is typed", () => {
    expect(names("192.168.1.20", OTHER)).toBe(true);
    expect(names(" HTTP://192.168.1.20:1234/ ", OTHER)).toBe(true);
    expect(names("192.168.1.2", OTHER)).toBe(false);
    expect(names("192.168.1.20:5000", OTHER)).toBe(false);
    expect(names("", OTHER)).toBe(false);
  });
});
