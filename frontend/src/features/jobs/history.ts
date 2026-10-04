import type { Job } from "@/api/client";

/** Consecutive jobs cancelled before they started (« Stop all ») shown as one row from this
 * many on. */
const MIN_GROUP = 3;

export type HistoryItem = { job: Job } | { skipped: Job[] };

/** The history with each run of jobs cancelled before starting folded into one item. */
export function groupHistory(jobs: Job[]): HistoryItem[] {
  const items: HistoryItem[] = [];
  let run: Job[] = [];
  const flush = (): void => {
    if (run.length >= MIN_GROUP) {
      items.push({ skipped: run });
    } else {
      items.push(...run.map((job) => ({ job })));
    }
    run = [];
  };
  for (const job of jobs) {
    if (job.status === "cancelled" && job.started_at === null) {
      run.push(job);
    } else {
      flush();
      items.push({ job });
    }
  }
  flush();
  return items;
}
