import { useTranslation } from "react-i18next";

import { CsvButton } from "./CsvButton";
import { ExportButton } from "./ExportButton";
import { OfflineActions } from "./OfflineActions";
import { TimelineBuildDialog } from "./TimelineBuildDialog";
import { clearVideos, selectVideos } from "./selection";

/** Tick every video shown (the filters decide which), or untick everything; export the analysis
 * files of the ticked videos, download a table of them (CSV), or make a timeline of them. */
export function SelectionBar({
  ids,
  offline = [],
  selected,
}: {
  ids: string[];
  offline?: string[]; // the videos shown whose file is gone
  selected: ReadonlySet<string>;
}) {
  const { t } = useTranslation();
  const shown = ids.filter((id) => selected.has(id)).length;
  const lost = offline.filter((id) => selected.has(id));
  const hidden = selected.size - shown;
  return (
    <div className="text-muted-foreground -mt-2 hidden flex-wrap items-center gap-x-4 gap-y-1 text-sm md:flex">
      <label className="hover:text-foreground flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={shown === ids.length}
          ref={(input) => {
            if (input) input.indeterminate = shown > 0 && shown < ids.length;
          }}
          onChange={(event) => {
            selectVideos(ids, event.target.checked);
          }}
          className="accent-primary size-4"
        />
        {t("library.selectShown", { count: ids.length })}
      </label>
      {selected.size > 0 ? (
        <span>
          {t("batch.selected", { count: selected.size })}
          {hidden > 0 ? ` (${t("library.hiddenSelected", { count: hidden })})` : ""} ·{" "}
          <button
            type="button"
            onClick={clearVideos}
            className="hover:text-foreground focus-visible:ring-ring rounded underline underline-offset-2 focus-visible:ring-2 focus-visible:outline-none"
          >
            {t("batch.clear")}
          </button>
        </span>
      ) : null}
      {selected.size > 0 ? <ExportButton ids={[...selected]} /> : null}
      {selected.size > 0 ? <CsvButton ids={[...selected]} /> : null}
      {selected.size > 0 ? <TimelineBuildDialog ids={[...selected]} /> : null}
      {lost.length > 0 ? <OfflineActions ids={lost} /> : null}
    </div>
  );
}
