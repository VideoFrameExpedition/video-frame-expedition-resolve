import type { StageFamily, StageInfo } from "@/api/client";

export const FAMILY_ORDER: StageFamily[] = [
  "file",
  "context",
  "image",
  "sound",
  "vision",
  "language",
  "search",
  "other",
];

export interface FamilyGroup {
  family: StageFamily;
  stages: StageInfo[];
}

/** Stages grouped by what they look at; execution order inside each group. */
export function groupByFamily(stages: readonly StageInfo[]): FamilyGroup[] {
  return FAMILY_ORDER.map((family) => ({
    family,
    stages: stages.filter((stage) => stage.family === family),
  })).filter((group) => group.stages.length > 0);
}

/** Stages left unticked that a ticked one needs: the analysis completes them when missing. */
export function requiredStages(
  stages: readonly StageInfo[],
  chosen: ReadonlySet<string>,
): Set<string> {
  const byName = new Map(stages.map((stage) => [stage.name, stage]));
  const needed = new Set<string>();
  const visit = (name: string): void => {
    for (const dep of byName.get(name)?.requires ?? []) {
      if (!needed.has(dep)) {
        needed.add(dep);
        visit(dep);
      }
    }
  };
  for (const name of chosen) visit(name);
  for (const name of chosen) needed.delete(name);
  return needed;
}

/** Stages that need the results of ``names``, directly or not (``requires``): a stage that runs
 * again drops their results, so they are redone with it. Catalogue order, ``names`` excluded. */
export function hardDependents(stages: readonly StageInfo[], names: ReadonlySet<string>): string[] {
  const found = new Set<string>();
  let frontier = [...names];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const stage of stages) {
      if (names.has(stage.name) || found.has(stage.name)) continue;
      if (stage.requires.some((dep) => frontier.includes(dep))) {
        found.add(stage.name);
        next.push(stage.name);
      }
    }
    frontier = next;
  }
  return stages.map((stage) => stage.name).filter((name) => found.has(name));
}

/** Whether redoing these stages is long: the vision model or speech recognition. */
export function isHeavy(stages: readonly StageInfo[], names: readonly string[]): boolean {
  return stages.some(
    (stage) =>
      names.includes(stage.name) && (stage.resource === "lmstudio" || stage.resource === "asr"),
  );
}

/** Whether a stage has a result that a rerun of its requirements redoes (as the analysis engine
 * decides): succeeded, skipped for good, or skipped only because stages being redone failed. */
export function hasResult(
  run: { status: string; summary: Record<string, unknown> },
  redone: ReadonlySet<string>,
): boolean {
  if (run.status === "succeeded") return true;
  if (run.status !== "skipped") return false;
  const blockedBy = run.summary.blocked_by;
  if (Array.isArray(blockedBy) && blockedBy.length > 0) {
    return blockedBy.every((name) => typeof name === "string" && redone.has(name));
  }
  return run.summary.retryable !== true;
}
