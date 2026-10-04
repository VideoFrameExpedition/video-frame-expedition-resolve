import "./styles/index.css";
import i18n from "./i18n";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { buildRouter } from "./app/router";
import { AccessGate } from "./features/access/AccessGate";
import { applyTheme } from "./lib/theme";

applyTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, refetchOnWindowFocus: false, retry: 1 },
  },
});
const router = buildRouter(queryClient);
// The analyses' texts come in the interface's language: read again in the new one.
i18n.on("languageChanged", () => {
  void queryClient.invalidateQueries();
});

const container = document.getElementById("root");
if (!container) {
  throw new Error("Élément #root introuvable");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AccessGate>
        <RouterProvider router={router} />
      </AccessGate>
    </QueryClientProvider>
  </StrictMode>,
);
