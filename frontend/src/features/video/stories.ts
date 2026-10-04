import type { Shot, ShotStory } from "@/api/client";

/** The part of a shot's story that covers ``t`` (else its first part); none before the stage ran. */
export function storyAt(stories: readonly ShotStory[], t: number): ShotStory | undefined {
  return stories.find((story) => t >= story.start_s && t < story.end_s) ?? stories[0];
}

/** The vision models that wrote the stories of these shots (usually one), for the footnote. */
export function storyModels(shots: readonly Shot[]): string[] {
  return [...new Set(shots.flatMap((shot) => shot.stories.map((story) => story.model)))];
}
