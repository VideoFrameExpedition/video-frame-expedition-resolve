import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import createClient from "openapi-fetch";
import type { ReactNode } from "react";

import type { AccessSession } from "@/api/access";
import type * as ClientModule from "@/api/client";
import { api, unwrap } from "@/api/client";
import type { paths } from "@/api/schema";

import { AccessGate, LogoutButton } from "./AccessGate";

// The real hooks and client, against a fake server (MSW).
vi.mock("@/api/client", async (original) => {
  const actual = await original<typeof ClientModule>();
  const fetch = (request: Request) => globalThis.fetch(request);
  return { ...actual, api: createClient<paths>({ baseUrl: "http://localhost", fetch }) };
});

const TOKEN = "le-bon-jeton";
const REMOTE: AccessSession = { local: false, required: true, authenticated: false, remote: true };
let session: AccessSession = REMOTE;
const logins: string[] = [];

const server = setupServer(
  http.get("http://localhost/api/v1/access/session", () => HttpResponse.json(session)),
  http.post("http://localhost/api/v1/access/login", async ({ request }) => {
    const { token } = (await request.json()) as { token: string };
    logins.push(token);
    if (token !== TOKEN) {
      return HttpResponse.json(
        { status: 401, code: "unauthorized", detail: "Jeton incorrect." },
        { status: 401 },
      );
    }
    session = { ...session, authenticated: true };
    return new HttpResponse(null, { status: 204 });
  }),
  http.post("http://localhost/api/v1/access/logout", () => {
    session = { ...session, authenticated: false };
    return new HttpResponse(null, { status: 204 });
  }),
  http.get("http://localhost/api/v1/jobs", () =>
    session.authenticated
      ? HttpResponse.json([])
      : HttpResponse.json({ status: 401, code: "unauthorized" }, { status: 401 }),
  ),
);

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});
afterAll(() => {
  server.close();
});
beforeEach(() => {
  session = REMOTE;
  logins.length = 0;
});

function Library() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => unwrap(api.GET("/api/v1/jobs")),
    enabled: false,
  });
  return (
    <div>
      <p>Bibliothèque</p>
      <button type="button" onClick={() => void jobs.refetch()}>
        Recharger
      </button>
      <LogoutButton />
    </div>
  );
}

function renderGate(children: ReactNode = <Library />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AccessGate>{children}</AccessGate>
    </QueryClientProvider>,
  );
}

describe("AccessGate", () => {
  it("opens straight away on the app's computer", async () => {
    session = { local: true, required: false, authenticated: true, remote: true };
    renderGate();
    expect(await screen.findByText("Bibliothèque")).toBeVisible();
    expect(screen.queryByLabelText("Jeton d'accès")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Se déconnecter" })).not.toBeInTheDocument();
  });

  it("asks another device for the token, then opens", async () => {
    const user = userEvent.setup();
    renderGate();
    expect(
      await screen.findByRole("heading", { name: "Connexion à Video Frame Expedition" }),
    ).toBeVisible();
    expect(screen.getByText(/commande « vfe token »/)).toBeVisible();
    expect(screen.queryByText("Bibliothèque")).not.toBeInTheDocument();

    const field = screen.getByLabelText("Jeton d'accès");
    expect(field).toHaveAttribute("type", "password");
    await user.type(field, "faux");
    await user.click(screen.getByRole("button", { name: "Se connecter" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Jeton incorrect.");

    await user.clear(field);
    await user.type(field, ` ${TOKEN} `);
    await user.click(screen.getByRole("button", { name: "Se connecter" }));
    expect(await screen.findByText("Bibliothèque")).toBeVisible();
    expect(logins).toEqual(["faux", TOKEN]);
  });

  it("comes back to the login page when the session ends", async () => {
    session = { ...REMOTE, authenticated: true };
    const user = userEvent.setup();
    renderGate();
    await user.click(await screen.findByRole("button", { name: "Recharger" }));
    session = { ...REMOTE, authenticated: false }; // token changed on the app's computer
    await user.click(screen.getByRole("button", { name: "Recharger" }));
    expect(await screen.findByLabelText("Jeton d'accès")).toBeVisible();
  });

  it("signs out", async () => {
    session = { ...REMOTE, authenticated: true };
    const user = userEvent.setup();
    renderGate();
    await user.click(await screen.findByRole("button", { name: "Se déconnecter" }));
    expect(await screen.findByLabelText("Jeton d'accès")).toBeVisible();
  });
});
