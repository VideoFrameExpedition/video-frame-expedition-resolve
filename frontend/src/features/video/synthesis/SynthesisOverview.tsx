import { errorMessage } from "@/api/client";
import { useSynthesis } from "@/api/queries";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";

import { ChapterList } from "./ChapterList";
import { HighlightList } from "./HighlightList";
import { SynthesisCard } from "./SynthesisCard";

/** Top of the overview tab: the synthesis card, then its chapters and suggested highlights. */
export function SynthesisOverview({ videoId, offline }: { videoId: string; offline: boolean }) {
  const synthesis = useSynthesis(videoId);
  if (synthesis.isPending) {
    return (
      <div className="bg-card grid gap-3 rounded-xl border p-6" aria-hidden>
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-6 w-2/3" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    );
  }
  if (synthesis.isError) {
    return (
      <Alert>
        <AlertDescription>{errorMessage(synthesis.error)}</AlertDescription>
      </Alert>
    );
  }
  const data = synthesis.data;
  return (
    <div className="grid gap-6">
      <SynthesisCard videoId={videoId} data={data} offline={offline} />
      <ChapterList chapters={data.chapters} model={data.model} />
      <HighlightList highlights={data.highlights} chapters={data.chapters} model={data.model} />
    </div>
  );
}
