import { FolderOpen, FolderPlus } from "lucide-react";
import { useId, useState, type SyntheticEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { errorMessage } from "@/api/client";
import { useAddRoot, usePickFolder } from "@/api/queries";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

export function AddRootDialog({ trigger }: { trigger?: React.ReactNode }) {
  const { t } = useTranslation();
  const ids = { path: useId(), label: useId(), focus: useId(), recursive: useId(), auto: useId() };
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState("");
  const [label, setLabel] = useState("");
  const [focus, setFocus] = useState("");
  const [recursive, setRecursive] = useState(true);
  const [autoAnalyze, setAutoAnalyze] = useState(true);
  const addRoot = useAddRoot();
  const picker = usePickFolder();

  // The desktop's folder dialog opens on this computer; the path comes back filled in, even when
  // this form was closed and reopened meanwhile (one dialog at a time: never a second request).
  const browse = (): void => {
    picker.mutate(undefined, {
      onSuccess: (picked) => {
        if (picked.path) setPath(picked.path);
      },
      onError: (error) => toast.error(errorMessage(error)),
    });
  };

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    addRoot.mutate(
      {
        path: path.trim(),
        label: label.trim() || null,
        analysis_focus: focus.trim() || null,
        recursive,
        auto_analyze: autoAnalyze,
      },
      {
        onSuccess: () => {
          toast.success(t("addRoot.success"));
          setOpen(false);
          setPath("");
          setLabel("");
          setFocus("");
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next && !path && !picker.isPending) browse(); // choosing the folder comes first
      }}
    >
      <DialogTrigger asChild>
        {trigger ?? (
          <Button>
            <FolderPlus className="size-4" />
            {t("library.addFolder")}
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-5">
          <DialogHeader>
            <DialogTitle>{t("addRoot.title")}</DialogTitle>
            <DialogDescription>{t("addRoot.description")}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor={ids.path}>{t("addRoot.path")}</Label>
            <div className="flex gap-2">
              <Input
                id={ids.path}
                value={path}
                onChange={(e) => {
                  setPath(e.target.value);
                }}
                placeholder={t("addRoot.pathPlaceholder")}
                required
                spellCheck={false}
              />
              <Button
                type="button"
                variant="secondary"
                onClick={browse}
                disabled={picker.isPending}
                className="shrink-0"
              >
                <FolderOpen className="size-4" />
                {picker.isPending ? t("addRoot.browsing") : t("addRoot.browse")}
              </Button>
            </div>
            <p className="text-muted-foreground text-xs">{t("addRoot.pathHelp")}</p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor={ids.label}>{t("addRoot.label")}</Label>
            <Input
              id={ids.label}
              value={label}
              onChange={(e) => {
                setLabel(e.target.value);
              }}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor={ids.focus}>{t("addRoot.focus")}</Label>
            <Textarea
              id={ids.focus}
              value={focus}
              onChange={(e) => {
                setFocus(e.target.value);
              }}
              rows={3}
            />
            <p className="text-muted-foreground text-xs">{t("addRoot.focusHelp")}</p>
          </div>
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor={ids.recursive}>{t("addRoot.recursive")}</Label>
            <Switch id={ids.recursive} checked={recursive} onCheckedChange={setRecursive} />
          </div>
          <div className="flex items-center justify-between gap-4">
            <div className="grid gap-1">
              <Label htmlFor={ids.auto}>{t("addRoot.autoAnalyze")}</Label>
              <p className="text-muted-foreground text-xs">{t("addRoot.autoAnalyzeHelp")}</p>
            </div>
            <Switch id={ids.auto} checked={autoAnalyze} onCheckedChange={setAutoAnalyze} />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setOpen(false);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={addRoot.isPending || path.trim().length < 3}>
              {t("addRoot.submit")}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
