import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Schemas } from "@/api/client";

import { LmStudioCard } from "./LmStudioCard";
import { names } from "./lmStudioAddress";

type Link = Schemas["LmStudioLinkOut"];
type Kind = Link["kind"];

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
  kind: null,
  found: "lmstudio",
  parallel: null,
  vision: true,
  past: [],
};
const OTHER = "http://192.168.1.20:1234";
const GPU_BOX = "http://gpu-box:8000/v1";
const past = (url: string, has_token = false, kind: Kind = null): Link["past"][number] => ({
  url,
  last_used_at: "2026-10-01T18:30:00Z",
  local: false,
  has_token,
  kind,
  parallel: kind === "openai" ? 8 : null,
  vision: true,
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
    expect(screen.getByText("Serveur de modèles")).toBeVisible();
    expect(screen.getByText("http://127.0.0.1:1234")).toBeVisible();
    expect(screen.getByText("cet ordinateur")).toBeVisible();
    expect(screen.getByText("LM Studio")).toBeVisible(); // the kind that answered
    expect(screen.getByText("répond")).toBeVisible();
    expect(
      screen.getByRole("radio", { name: "Sur cet ordinateur (http://127.0.0.1:1234)" }),
    ).toBeChecked();
    expect(screen.queryByLabelText("Adresse du serveur")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Le trouver tout seul/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Aucune pour l'instant/)).toBeVisible();
  });

  it("names no kind before a server has answered", () => {
    link = { ...HERE, found: null };
    render(<LmStudioCard />);
    expect(screen.queryByText("LM Studio")).not.toBeInTheDocument();
    expect(screen.queryByText("Compatible OpenAI")).not.toBeInTheDocument();
  });

  it("tries another computer, then talks to it", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<LmStudioCard />);
    const there = screen.getByRole("radio", { name: "À une autre adresse" });
    expect(there).toHaveAccessibleDescription(/vLLM sur cet ordinateur : 127\.0\.0\.1:8000/);
    await user.click(there);
    // The frames leave this computer: said before anything is saved.
    expect(screen.getByText(/seront envoyées à cet ordinateur/)).toBeVisible();
    // The kind is found out unless said: LM Studio first.
    expect(
      screen.getByRole("radio", { name: "Le trouver tout seul (LM Studio d'abord)" }),
    ).toBeChecked();
    expect(screen.queryByLabelText("Requêtes envoyées à la fois")).not.toBeInTheDocument();
    const test_ = screen.getByRole("button", { name: "Tester" });
    expect(test_).toBeDisabled();
    const address = screen.getByLabelText("Adresse du serveur");
    expect(address).toHaveAttribute("placeholder", "192.168.1.20");
    expect(address).toHaveAccessibleDescription(/Serve on Local Network/);
    await user.type(address, " 192.168.1.20 ");
    await user.click(test_);
    expect(test).toHaveBeenCalledWith({ address: "192.168.1.20", kind: "auto" });

    tried = {
      url: OTHER,
      ok: true,
      local: false,
      kind: "lmstudio",
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
      { address: "192.168.1.20", kind: "auto", token: "secret" },
      expect.anything(),
    );
  });

  it("talks to an OpenAI-compatible server, with its own settings", async () => {
    const user = userEvent.setup();
    render(<LmStudioCard />);
    await user.click(screen.getByRole("radio", { name: "À une autre adresse" }));
    await user.click(screen.getByRole("radio", { name: "Compatible OpenAI (vLLM, llama.cpp…)" }));
    // Its port and its path are its own.
    const address = screen.getByLabelText("Adresse du serveur");
    expect(address).toHaveAttribute("placeholder", "192.168.1.20:8000");
    expect(address).toHaveAccessibleDescription(/Le port et le chemin sont gardés tels quels/);
    // Only such a server is told how many requests it takes, and whether it sees images.
    const requests = screen.getByLabelText("Requêtes envoyées à la fois");
    expect(requests).toHaveValue(4);
    expect(requests).toHaveAccessibleDescription(/vLLM traite les requêtes par lots/);
    const images = screen.getByRole("checkbox", { name: "Ce serveur voit les images" });
    expect(images).toBeChecked();
    expect(images).toHaveAccessibleDescription(/les Questions marchent quand même/);

    await user.type(address, "gpu-box:8000");
    await user.clear(requests);
    await user.type(requests, "8");
    await user.click(images);
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(choose).toHaveBeenCalledWith(
      { address: "gpu-box:8000", kind: "openai", parallel: 8, vision: false },
      expect.anything(),
    );

    // Found out: nothing but the address and the kind.
    await user.click(
      screen.getByRole("radio", { name: "Le trouver tout seul (LM Studio d'abord)" }),
    );
    expect(screen.queryByLabelText("Requêtes envoyées à la fois")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("checkbox", { name: "Ce serveur voit les images" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Tester" }));
    expect(test).toHaveBeenCalledWith({ address: "gpu-box:8000", kind: "auto" });
  });

  it("starts from the server in use, and says whether its model sees images", () => {
    link = {
      ...HERE,
      url: GPU_BOX,
      custom: true,
      local: false,
      kind: "openai",
      found: "openai",
      parallel: 8,
      vision: true,
      past: [past(GPU_BOX, false, "openai")],
    };
    tried = {
      url: GPU_BOX,
      ok: true,
      local: false,
      kind: "openai",
      models: 2,
      vision_models: 1,
      loaded: ["Qwen3-VL-8B-Instruct-AWQ"],
      images: true,
    };
    const { rerender } = render(<LmStudioCard />);
    expect(screen.getByText("Adresse utilisée").closest("p")).toHaveTextContent(
      "Compatible OpenAI",
    );
    expect(
      screen.getByRole("radio", { name: "Compatible OpenAI (vLLM, llama.cpp…)" }),
    ).toBeChecked();
    expect(screen.getByLabelText("Adresse du serveur")).toHaveValue(GPU_BOX);
    expect(screen.getByLabelText("Requêtes envoyées à la fois")).toHaveValue(8);
    expect(
      screen.getByText(
        /Un serveur compatible OpenAI répond sur http:\/\/gpu-box:8000\/v1 : 2 modèles, dont 1 de vision/,
      ),
    ).toHaveTextContent(
      "Servi : Qwen3-VL-8B-Instruct-AWQ. Son modèle de vision a bien vu l'image d'essai.",
    );

    tried = { ...tried, images: false };
    rerender(<LmStudioCard />);
    expect(screen.queryByText(/a bien vu l'image d'essai/)).not.toBeInTheDocument();
    expect(
      screen.getByText(/Son modèle refuse les images : décochez « Ce serveur voit les images »/),
    ).toBeVisible();
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
    expect(screen.getByLabelText("Adresse du serveur")).toHaveValue(OTHER);
  });

  it("keeps the past connections one click away", async () => {
    const user = userEvent.setup();
    link = {
      ...HERE,
      url: OTHER,
      custom: true,
      local: false,
      has_token: true,
      past: [past(OTHER, true), past("http://pc-salon:5000"), past(GPU_BOX, false, "openai")],
    };
    render(<LmStudioCard />);
    const list = within(screen.getByRole("region", { name: "Connexions passées" }));
    const [current, older, served] = list.getAllByRole("listitem") as [
      HTMLElement,
      HTMLElement,
      HTMLElement,
    ];
    // The one in use is neither chosen again nor forgotten.
    expect(current).toHaveTextContent("utilisée en ce moment");
    expect(current).toHaveTextContent("jeton enregistré");
    expect(within(current).queryByRole("button")).not.toBeInTheDocument();
    expect(older).toHaveTextContent(/utilisée le 1 oct\. 2026/);
    expect(older).not.toHaveTextContent("Compatible OpenAI");
    expect(served).toHaveTextContent("Compatible OpenAI");
    // Its token is kept unless another one is typed.
    expect(screen.getByLabelText("Jeton d'API (facultatif)")).toHaveAccessibleDescription(
      /laissez vide pour le garder/,
    );

    await user.click(list.getByRole("button", { name: "Utiliser http://pc-salon:5000" }));
    expect(choose).toHaveBeenCalledWith({ address: "http://pc-salon:5000" }, expect.anything());
    // The kind and the settings saved for an address come back with it.
    await user.click(list.getByRole("button", { name: `Utiliser ${GPU_BOX}` }));
    expect(choose).toHaveBeenLastCalledWith({ address: GPU_BOX }, expect.anything());
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

  it("recognises an OpenAI-compatible server's address, completed with /v1", () => {
    expect(names("gpu-box:8000", GPU_BOX)).toBe(true);
    expect(names("http://gpu-box:8000/v1/", GPU_BOX)).toBe(true);
    expect(names("gpu-box:800", GPU_BOX)).toBe(false);
    expect(names("gpu-box", GPU_BOX)).toBe(false);
    // A path of its own is kept.
    const proxied = "http://gpu-box/llm/v1";
    expect(names("http://gpu-box/llm/v1", proxied)).toBe(true);
    expect(names("gpu-box/llm", proxied)).toBe(true);
    expect(names("gpu-box", proxied)).toBe(false);
  });
});
