import type { StageInfo } from "@/api/client";

import { hardDependents, hasResult, isHeavy, requiredStages } from "./stagePlan";

const stage = (name: string, requires: string[], resource: StageInfo["resource"] = "cpu") =>
  ({ name, requires, resource, family: "image", after: [], optional: false }) as StageInfo;
const STAGES = [
  stage("probe", []),
  stage("keyframes", ["probe"]),
  stage("vision_frames", ["keyframes"], "lmstudio"),
  stage("transcript", ["probe"], "asr"),
];

describe("stage plan", () => {
  it("finds what a ticked stage needs, and what needs a redone one", () => {
    expect([...requiredStages(STAGES, new Set(["vision_frames"]))]).toEqual(["keyframes", "probe"]);
    expect(hardDependents(STAGES, new Set(["probe"]))).toEqual([
      "keyframes",
      "vision_frames",
      "transcript",
    ]);
    expect(hardDependents(STAGES, new Set(["transcript"]))).toEqual([]);
    expect(isHeavy(STAGES, ["keyframes"])).toBe(false);
    expect(isHeavy(STAGES, ["keyframes", "transcript"])).toBe(true);
  });

  it("agrees with the engine on which stages have a result", () => {
    const redone = new Set(["keyframes"]);
    expect(hasResult({ status: "succeeded", summary: {} }, redone)).toBe(true);
    expect(hasResult({ status: "skipped", summary: { retryable: false } }, redone)).toBe(true);
    expect(hasResult({ status: "skipped", summary: { retryable: true } }, redone)).toBe(false);
    expect(
      hasResult(
        { status: "skipped", summary: { retryable: true, blocked_by: ["keyframes"] } },
        redone,
      ),
    ).toBe(true);
    expect(
      hasResult({ status: "skipped", summary: { retryable: true, blocked_by: ["probe"] } }, redone),
    ).toBe(false);
    expect(hasResult({ status: "failed", summary: {} }, redone)).toBe(false);
  });
});
