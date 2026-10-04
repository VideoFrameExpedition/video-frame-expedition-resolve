import { LogOut } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { useAccessSession, useLogout, useUnauthorizedWatcher } from "@/api/access";
import { errorMessage } from "@/api/client";
import { Button } from "@/components/ui/button";

import { LoginPage } from "./LoginPage";

/** The interface, or the login page for another device without a session. This
 * computer never sees the login page; an older backend without the endpoint neither. */
export function AccessGate({ children }: { children: ReactNode }) {
  const session = useAccessSession();
  useUnauthorizedWatcher();
  if (session.isPending) {
    return <div className="min-h-svh" aria-busy="true" />;
  }
  if (session.data && !session.data.authenticated) {
    return <LoginPage />;
  }
  return children;
}

/** « Sign out », in the header, for a session opened with the token. */
export function LogoutButton() {
  const { t } = useTranslation();
  const session = useAccessSession();
  const logout = useLogout();
  if (!session.data?.required) {
    return null;
  }
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={t("access.logout")}
      disabled={logout.isPending}
      onClick={() => {
        logout.mutate(undefined, { onError: (error) => toast.error(errorMessage(error)) });
      }}
    >
      <LogOut aria-hidden />
      <span className="hidden sm:inline">{t("access.logout")}</span>
    </Button>
  );
}
