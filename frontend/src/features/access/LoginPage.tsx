import { KeyRound, LogIn } from "lucide-react";
import { useState, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";

import { useLogin } from "@/api/access";
import { errorMessage } from "@/api/client";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Shown to another device of the tailnet until it gives the token. */
export function LoginPage() {
  const { t } = useTranslation();
  const login = useLogin();
  const [token, setToken] = useState("");

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    if (token.trim()) {
      login.mutate(token.trim());
    }
  };

  return (
    <main className="flex min-h-svh items-center justify-center px-4 py-10">
      <div className="glass grid w-full max-w-md gap-6 rounded-xl border p-6 shadow-sm">
        <Logo className="h-16" />
        <div className="grid gap-2">
          <h1 className="text-xl font-semibold tracking-tight">{t("access.loginTitle")}</h1>
          <p className="text-muted-foreground text-sm">{t("access.loginIntro")}</p>
        </div>
        <form className="grid gap-3" onSubmit={submit}>
          <Label htmlFor="access-token">{t("access.tokenLabel")}</Label>
          <Input
            id="access-token"
            type="password"
            autoComplete="current-password"
            spellCheck={false}
            value={token}
            aria-invalid={login.isError}
            aria-describedby={login.isError ? "access-error" : "access-where"}
            onChange={(event) => {
              setToken(event.target.value);
            }}
          />
          {login.isError ? (
            <p id="access-error" role="alert" className="text-destructive text-sm">
              {errorMessage(login.error)}
            </p>
          ) : null}
          <Button type="submit" disabled={!token.trim() || login.isPending}>
            <LogIn aria-hidden />
            {login.isPending ? t("access.loggingIn") : t("access.submit")}
          </Button>
        </form>
        <p id="access-where" className="text-muted-foreground flex gap-2 text-xs">
          <KeyRound className="mt-0.5 size-4 shrink-0" aria-hidden />
          {t("access.where")}
        </p>
      </div>
    </main>
  );
}
