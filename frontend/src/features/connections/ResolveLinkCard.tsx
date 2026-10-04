import { Monitor, Plus, Trash2 } from "lucide-react";
import { useId, useState, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage, type Schemas } from "@/api/client";
import { useAnalysisSettings, usePatchAnalysisSettings, useResolveProject } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";

type Folder = Schemas["ResolveFolder"];

/** Where DaVinci Resolve runs: this computer, or another one reading the rushes
 * through a share, and the same folders seen from both. */
export function ResolveLinkCard() {
  const { t } = useTranslation();
  const settings = useAnalysisSettings();
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Monitor className="text-brand-teal size-4" aria-hidden />
          {t("connections.resolveLink.title")}
        </CardTitle>
        <CardDescription>{t("connections.resolveLink.help")}</CardDescription>
      </CardHeader>
      <CardContent>
        {settings.data ? (
          <ResolveLinkForm
            host={settings.data.resolve_host ?? null}
            folders={settings.data.resolve_folders ?? []}
          />
        ) : (
          <Skeleton className="h-40" />
        )}
      </CardContent>
    </Card>
  );
}

function ResolveLinkForm({ host, folders }: { host: string | null; folders: Folder[] }) {
  const { t } = useTranslation();
  const patch = usePatchAnalysisSettings();
  const [elsewhere, setElsewhere] = useState(host !== null);
  const [address, setAddress] = useState(host ?? "");
  const [pairs, setPairs] = useState<Folder[]>(folders);
  const hostId = useId();

  const save = (event: SyntheticEvent): void => {
    event.preventDefault();
    const kept = pairs
      .map((pair) => ({ here: pair.here.trim(), there: pair.there.trim() }))
      .filter((pair) => pair.here && pair.there);
    patch.mutate(
      { resolve_host: elsewhere ? address.trim() || null : null, resolve_folders: kept },
      {
        onSuccess: () => toast.success(t("connections.resolveLink.saved")),
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  const setPair = (index: number, side: keyof Folder, value: string): void => {
    setPairs(pairs.map((pair, i) => (i === index ? { ...pair, [side]: value } : pair)));
  };

  return (
    <form onSubmit={save} className="grid gap-5">
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">{t("connections.resolveLink.where")}</legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="resolve-where"
            checked={!elsewhere}
            onChange={() => {
              setElsewhere(false);
            }}
            className="accent-primary"
          />
          {t("connections.resolveLink.here")}
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="radio"
            name="resolve-where"
            checked={elsewhere}
            onChange={() => {
              setElsewhere(true);
            }}
            className="accent-primary"
          />
          {t("connections.resolveLink.there")}
        </label>
        {elsewhere ? (
          <div className="grid gap-1 pl-6">
            <Label htmlFor={hostId}>{t("connections.resolveLink.address")}</Label>
            <Input
              id={hostId}
              value={address}
              placeholder="100.64.12.34"
              onChange={(event) => {
                setAddress(event.target.value);
              }}
              className="max-w-xs font-mono"
            />
            <p className="text-muted-foreground text-xs">
              {t("connections.resolveLink.addressHint")}
            </p>
          </div>
        ) : null}
      </fieldset>

      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">{t("connections.resolveLink.folders")}</legend>
        <p className="text-muted-foreground text-xs">{t("connections.resolveLink.foldersHint")}</p>
        {pairs.map((pair, index) => (
          <div
            key={index}
            className="grid grid-cols-[minmax(0,1fr)] items-center gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
          >
            <Input
              aria-label={t("connections.resolveLink.hereFolder", { n: index + 1 })}
              value={pair.here}
              placeholder="D:\cats 2026"
              onChange={(event) => {
                setPair(index, "here", event.target.value);
              }}
              className="font-mono text-xs"
            />
            <Input
              aria-label={t("connections.resolveLink.thereFolder", { n: index + 1 })}
              value={pair.there}
              placeholder="/Volumes/cats 2026"
              onChange={(event) => {
                setPair(index, "there", event.target.value);
              }}
              className="font-mono text-xs"
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("connections.resolveLink.remove", { n: index + 1 })}
              onClick={() => {
                setPairs(pairs.filter((_, i) => i !== index));
              }}
            >
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="justify-self-start"
          onClick={() => {
            setPairs([...pairs, { here: "", there: "" }]);
          }}
        >
          <Plus className="size-4" />
          {t("connections.resolveLink.add")}
        </Button>
      </fieldset>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={patch.isPending}>
          {t("common.save")}
        </Button>
        <ResolveTest />
      </div>
      <p className="text-muted-foreground text-xs">{t("connections.resolveLink.note")}</p>
    </form>
  );
}

/** Read the project open in Resolve, where it is set to run (after saving). */
function ResolveTest() {
  const { t } = useTranslation();
  const [asked, setAsked] = useState(false);
  const probe = useResolveProject(asked);
  const result = probe.data;
  return (
    <>
      <Button
        type="button"
        variant="outline"
        disabled={probe.isFetching}
        onClick={() => {
          if (asked) void probe.refetch();
          else setAsked(true);
        }}
      >
        {t("connections.resolveLink.test")}
      </Button>
      <span role="status" className="text-xs">
        {probe.isFetching ? (
          <span className="text-muted-foreground">{t("connections.resolveLink.testing")}</span>
        ) : probe.isError ? (
          <span className="text-destructive">{errorMessage(probe.error)}</span>
        ) : result ? (
          <span className="text-brand-teal">
            {t("connections.resolveLink.ok", {
              product: result.product,
              version: result.version,
              project: result.project.name,
            })}
          </span>
        ) : null}
      </span>
    </>
  );
}
