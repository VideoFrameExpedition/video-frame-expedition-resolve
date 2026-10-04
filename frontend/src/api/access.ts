import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Middleware } from "openapi-fetch";
import { useEffect } from "react";

import { api, unwrap, type Schemas } from "./client";

/** Access from other devices: session, login, logout, connection help. */
export type AccessSession = Schemas["SessionOut"];
export type Connections = Schemas["ConnectionsOut"];

export const accessKeys = {
  session: ["access", "session"] as const,
  connections: ["access", "connections"] as const,
};

export function useAccessSession() {
  return useQuery({
    queryKey: accessKeys.session,
    queryFn: () => unwrap(api.GET("/api/v1/access/session")),
    staleTime: Infinity,
    retry: 1,
  });
}

export function useConnections() {
  return useQuery({
    queryKey: accessKeys.connections,
    queryFn: () => unwrap(api.GET("/api/v1/access/connections")),
  });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => unwrap(api.POST("/api/v1/access/login", { body: { token } })),
    onSuccess: () => client.invalidateQueries({ queryKey: accessKeys.session }),
  });
}

/** Close this browser's session: back to the login page, nothing of the library kept. */
export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/access/logout")),
    onSuccess: () => {
      client.setQueryData<AccessSession>(accessKeys.session, (session) =>
        session ? { ...session, authenticated: false } : session,
      );
      client.removeQueries({ predicate: (query) => query.queryKey[0] !== "access" });
    },
  });
}

/** A 401 from the API (session expired, token changed): ask again for the token. */
export function useUnauthorizedWatcher(): void {
  const client = useQueryClient();
  useEffect(() => {
    const watcher: Middleware = {
      onResponse({ response, request }) {
        if (response.status === 401 && !request.url.includes("/api/v1/access/")) {
          void client.invalidateQueries({ queryKey: accessKeys.session });
        }
        return undefined;
      },
    };
    api.use(watcher);
    return () => {
      api.eject(watcher);
    };
  }, [client]);
}
