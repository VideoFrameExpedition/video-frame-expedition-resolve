import { ChevronRight, Folder, FolderOpen, Layers } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

import { FamilyDot } from "./CodeBadges";
import type { ModelFamily, TreeSpot } from "./modelCodes";

function rowClass(active: boolean): string {
  return cn(
    "text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:ring-ring flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:outline-none",
    active && "bg-secondary text-foreground font-medium",
  );
}

/** How many models a place holds, and how many of them are ticked. */
function Counts({ count, ticked }: { count: number; ticked: number }) {
  const { t } = useTranslation();
  return (
    <span className="ml-auto flex shrink-0 items-center gap-1.5 pl-1">
      {ticked > 0 ? (
        <span
          className="bg-accent text-accent-foreground rounded-full px-1.5 font-mono text-[0.7rem]"
          title={t("bench.tree.ticked", { count: ticked })}
        >
          {ticked}
        </span>
      ) : null}
      <span className="text-muted-foreground font-mono text-xs">{count}</span>
    </span>
  );
}

/** The models as LM Studio keeps them in its folders: every model, each family, each folder
 * of a family. Choosing one shows its models beside the tree. */
export function ModelTree({
  tree,
  spot,
  ticked,
  onChoose,
}: {
  tree: readonly ModelFamily[];
  spot: TreeSpot;
  ticked: ReadonlySet<string>;
  onChoose: (spot: TreeSpot) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<ReadonlySet<string>>(
    () => new Set(spot.family !== undefined ? [spot.family] : []),
  );
  const toggle = (family: string, next: boolean): void => {
    setOpen((before) => {
      const after = new Set(before);
      if (next) after.add(family);
      else after.delete(family);
      return after;
    });
  };
  const tickedIn = (keys: string[]): number => keys.filter((key) => ticked.has(key)).length;
  const keysOf = (family: ModelFamily): string[] =>
    family.folders.flatMap((folder) => folder.models.map((model) => model.key));
  const total = tree.reduce((sum, family) => sum + family.count, 0);
  const all = spot.family === undefined;

  return (
    <nav aria-label={t("bench.tree.title")} className="grid grid-cols-1 content-start gap-0.5">
      <button
        type="button"
        onClick={() => {
          onChoose({});
        }}
        aria-current={all ? "page" : undefined}
        aria-label={t("bench.tree.label", { name: t("bench.tree.all"), count: total })}
        className={rowClass(all)}
      >
        <Layers className="text-brand-teal size-4 shrink-0" aria-hidden />
        <span className="truncate">{t("bench.tree.all")}</span>
        <Counts count={total} ticked={ticked.size} />
      </button>
      <ul className="grid grid-cols-1 gap-0.5">
        {tree.map((family) => {
          const isOpen = open.has(family.name) && family.folders.length > 0;
          const name = family.name || t("bench.tree.noFamily");
          const active = spot.family === family.name && spot.folder === undefined;
          return (
            <li key={family.name}>
              <div className="flex items-center">
                <button
                  type="button"
                  onClick={() => {
                    toggle(family.name, !isOpen);
                  }}
                  aria-expanded={isOpen}
                  aria-label={t(isOpen ? "bench.tree.collapse" : "bench.tree.expand", { name })}
                  className="text-muted-foreground hover:text-foreground focus-visible:ring-ring flex size-5 shrink-0 items-center justify-center rounded focus-visible:ring-2 focus-visible:outline-none"
                >
                  <ChevronRight
                    className={cn("size-3.5 transition-transform", isOpen && "rotate-90")}
                  />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    toggle(family.name, true);
                    onChoose({ family: family.name });
                  }}
                  aria-current={active ? "page" : undefined}
                  aria-label={t("bench.tree.label", { name, count: family.count })}
                  className={rowClass(active)}
                >
                  <FamilyDot family={family.name} />
                  <span className="truncate">{name}</span>
                  <Counts count={family.count} ticked={tickedIn(keysOf(family))} />
                </button>
              </div>
              {isOpen ? (
                <ul className="grid grid-cols-1 gap-0.5 pl-5">
                  {family.folders.map((folder) => {
                    const here = spot.family === family.name && spot.folder === folder.name;
                    const Icon = here ? FolderOpen : Folder;
                    return (
                      <li key={folder.name} className="flex">
                        <button
                          type="button"
                          onClick={() => {
                            onChoose({ family: family.name, folder: folder.name });
                          }}
                          aria-current={here ? "page" : undefined}
                          aria-label={t("bench.tree.label", {
                            name: folder.name,
                            count: folder.models.length,
                          })}
                          title={folder.name}
                          className={rowClass(here)}
                        >
                          <Icon className="text-brand-teal size-4 shrink-0" aria-hidden />
                          <span className="truncate font-mono text-xs">{folder.name}</span>
                          <Counts
                            count={folder.models.length}
                            ticked={tickedIn(folder.models.map((model) => model.key))}
                          />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
