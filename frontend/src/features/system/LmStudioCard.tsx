import { Server, Trash2 } from "lucide-react";
import { useId, useState, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type Schemas } from "@/api/client";
import {
  useChooseLmStudio,
  useForgetLmStudio,
  useLmModels,
  useLmStudioLink,
  useTestLmStudio,
} from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

import { names } from "./lmStudioAddress";

type Link = Schemas["LmStudioLinkOut"];
type Tried = Schemas["LmStudioTestOut"];

/** Where LM Studio runs: this computer, or another one of the user's network, tried
 * before it is chosen; the addresses used before stay one click away. */
export function LmStudioCard() {
  const { t } = useTranslation();
  const link = useLmStudioLink();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Server className="text-brand-teal size-4" aria-hidden />
          {t("system.lmstudioLink.title")}
        </CardTitle>
        <CardDescription>{t("system.lmstudioLink.help")}</CardDescription>
      </CardHeader>
      <CardContent>
        {link.data ? (
          // A change of address made elsewhere (a past connection) starts the form afresh.
          <LmStudioForm key={link.data.url} link={link.data} />
        ) : (
          <Skeleton className="h-40" />
        )}
      </CardContent>
    </Card>
  );
}

function LmStudioForm({ link }: { link: Link }) {
  const { t } = useTranslation();
  const models = useLmModels();
  const choose = useChooseLmStudio();
  const test = useTestLmStudio();
  const [elsewhere, setElsewhere] = useState(link.custom);
  const [address, setAddress] = useState(link.custom ? link.url : "");
  const [token, setToken] = useState("");
  const addressId = useId();
  const addressHintId = useId();
  const tokenId = useId();
  const tokenHintId = useId();

  // An empty token field keeps the token remembered for the address.
  const choice = (): Schemas["LmStudioChoice"] => ({
    address: elsewhere ? address.trim() : null,
    ...(elsewhere && token.trim() ? { token: token.trim() } : {}),
  });
  const apply = (body: Schemas["LmStudioChoice"]): void => {
    choose.mutate(body, {
      onSuccess: (saved) => toast.success(t("system.lmstudioLink.saved", { url: saved.url })),
      onError: (error) => toast.error(errorMessage(error)),
    });
  };
  const save = (event: SyntheticEvent): void => {
    event.preventDefault();
    apply(choice());
  };
  const tokenKept = link.past.some((past) => past.has_token && names(address, past.url));

  return (
    <div className="grid gap-5">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">{t("system.lmstudioLink.current")}</span>
        <span className="font-mono">{link.url}</span>
        <Badge variant="secondary">
          {t(link.local ? "system.lmstudioLink.thisComputer" : "system.lmstudioLink.otherComputer")}
        </Badge>
        {models.isPending ? null : (
          <span role="status" className={models.isError ? "text-destructive" : "text-brand-teal"}>
            {t(models.isError ? "system.lmstudioLink.silent" : "system.lmstudioLink.answers")}
          </span>
        )}
      </p>

      <form onSubmit={save} className="grid gap-4">
        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-medium">{t("system.lmstudioLink.where")}</legend>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="lmstudio-where"
              checked={!elsewhere}
              onChange={() => {
                setElsewhere(false);
                test.reset();
              }}
              className="accent-primary"
            />
            {t("system.lmstudioLink.here", { url: link.default_url })}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              name="lmstudio-where"
              checked={elsewhere}
              onChange={() => {
                setElsewhere(true);
                test.reset();
              }}
              className="accent-primary"
            />
            {t("system.lmstudioLink.there")}
          </label>
          {elsewhere ? (
            <div className="grid gap-3 pl-6">
              <div className="grid gap-1">
                <Label htmlFor={addressId}>{t("system.lmstudioLink.address")}</Label>
                <Input
                  id={addressId}
                  aria-describedby={addressHintId}
                  value={address}
                  required
                  placeholder="192.168.1.20"
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => {
                    setAddress(event.target.value);
                    test.reset();
                  }}
                  className="max-w-xs font-mono"
                />
                <p id={addressHintId} className="text-muted-foreground text-xs">
                  {t("system.lmstudioLink.addressHint")}
                </p>
              </div>
              <div className="grid gap-1">
                <Label htmlFor={tokenId}>{t("system.lmstudioLink.token")}</Label>
                <Input
                  id={tokenId}
                  aria-describedby={tokenHintId}
                  type="password"
                  value={token}
                  autoComplete="off"
                  onChange={(event) => {
                    setToken(event.target.value);
                    test.reset();
                  }}
                  className="max-w-xs font-mono"
                />
                <p id={tokenHintId} className="text-muted-foreground text-xs">
                  {t(tokenKept ? "system.lmstudioLink.tokenKept" : "system.lmstudioLink.tokenHint")}
                </p>
              </div>
              <p className="text-warning text-xs">{t("system.lmstudioLink.privacy")}</p>
            </div>
          ) : null}
        </fieldset>

        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={choose.isPending}>
            {t("common.save")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={test.isPending || (elsewhere && !address.trim())}
            onClick={() => {
              test.mutate(choice());
            }}
          >
            {t("system.lmstudioLink.test")}
          </Button>
          <span role="status" className="min-w-0 text-xs">
            {test.isPending ? (
              <span className="text-muted-foreground">{t("system.lmstudioLink.testing")}</span>
            ) : test.isError ? (
              <span className="text-destructive">{errorMessage(test.error)}</span>
            ) : test.data ? (
              <TestResult tried={test.data} />
            ) : null}
          </span>
        </div>
      </form>

      <section aria-label={t("system.lmstudioLink.past")} className="grid gap-2">
        <h3 className="text-sm font-medium">{t("system.lmstudioLink.past")}</h3>
        {link.past.length === 0 ? (
          <p className="text-muted-foreground text-xs">{t("system.lmstudioLink.pastEmpty")}</p>
        ) : (
          <ul className="divide-border divide-y">
            {link.past.map((past) => (
              <PastConnection
                key={past.url}
                past={past}
                current={link.custom && past.url === link.url}
                busy={choose.isPending}
                onUse={() => {
                  apply({ address: past.url });
                }}
              />
            ))}
          </ul>
        )}
      </section>

      <p className="text-muted-foreground text-xs">{t("system.lmstudioLink.note")}</p>
    </div>
  );
}

function TestResult({ tried }: { tried: Tried }) {
  const { t } = useTranslation();
  if (!tried.ok) {
    return <span className="text-destructive">{tried.error}</span>;
  }
  const loaded = tried.loaded ?? [];
  return (
    <span className="text-brand-teal">
      {t("system.lmstudioLink.ok", {
        url: tried.url,
        count: tried.models,
        vision: tried.vision_models,
      })}{" "}
      {loaded.length > 0
        ? t("system.lmstudioLink.loaded", { models: loaded.join(", ") })
        : t("system.lmstudioLink.noneLoaded")}
    </span>
  );
}

function PastConnection({
  past,
  current,
  busy,
  onUse,
}: {
  past: Link["past"][number];
  current: boolean;
  busy: boolean;
  onUse: () => void;
}) {
  const { t, i18n } = useTranslation();
  const forget = useForgetLmStudio();
  const when = new Date(past.last_used_at).toLocaleString(i18n.language, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
      <span className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="font-mono break-all">{past.url}</span>
        {past.local ? (
          <Badge variant="secondary">{t("system.lmstudioLink.thisComputer")}</Badge>
        ) : null}
        {past.has_token ? (
          <Badge variant="secondary">{t("system.lmstudioLink.hasToken")}</Badge>
        ) : null}
        <span className="text-muted-foreground text-xs">
          {current ? t("system.lmstudioLink.inUse") : t("system.lmstudioLink.usedOn", { when })}
        </span>
      </span>
      {current ? null : (
        <span className="flex items-center gap-1">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={busy}
            aria-label={t("system.lmstudioLink.useThis", { url: past.url })}
            onClick={onUse}
          >
            {t("system.lmstudioLink.use")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={forget.isPending}
            aria-label={t("system.lmstudioLink.forget", { url: past.url })}
            onClick={() => {
              forget.mutate(past.url, {
                onError: (error) => toast.error(errorMessage(error)),
              });
            }}
          >
            <Trash2 className="size-4" />
          </Button>
        </span>
      )}
    </li>
  );
}
