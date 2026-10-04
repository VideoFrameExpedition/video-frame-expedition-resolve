import { createContext, useContext, useSyncExternalStore } from "react";

/**
 * Playback position shared by the timeline, the charts and the keyframe grid.
 *
 * It lives outside React state so that the 60 Hz updates during playback only re-render the
 * components that subscribe to it (and, through ``usePlayheadSelector``, only when the derived
 * value actually changes).
 */
export class PlayheadStore {
  private time: number;
  private readonly listeners = new Set<() => void>();

  constructor(initial = 0) {
    this.time = initial;
  }

  readonly get = (): number => this.time;

  set(time: number): void {
    if (Number.isFinite(time) && time !== this.time) {
      this.time = time;
      for (const listener of this.listeners) {
        listener();
      }
    }
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
}

export interface PlayerApi {
  playhead: PlayheadStore;
  duration: number;
  /** Move the player (and the playhead) to ``t`` seconds. */
  seek: (t: number) => void;
}

export const PlayerContext = createContext<PlayerApi | null>(null);

/** The video element's id: the lists below it bring it back into view when they seek. */
export const PLAYER_ELEMENT_ID = "video-player";

/** Show the player after a seek from far below it (the lists would otherwise show nothing). */
export function revealPlayer(): void {
  document
    .getElementById(PLAYER_ELEMENT_ID)
    ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

export function usePlayer(): PlayerApi {
  const player = useContext(PlayerContext);
  if (!player) {
    throw new Error("usePlayer must be used inside a PlayerContext provider");
  }
  return player;
}

export function usePlayheadTime(): number {
  const { playhead } = usePlayer();
  return useSyncExternalStore(playhead.subscribe, playhead.get);
}

/** Subscribe to a value derived from the playhead; ``select`` must return a primitive. */
export function usePlayheadSelector<T extends string | number | boolean | undefined>(
  select: (time: number) => T,
): T {
  const { playhead } = usePlayer();
  return useSyncExternalStore(playhead.subscribe, () => select(playhead.get()));
}

/** Keep ``store`` in sync with a <video>: every animation frame while playing, else on events. */
export function bindVideoElement(video: HTMLVideoElement, store: PlayheadStore): () => void {
  let frame = 0;
  const tick = (): void => {
    store.set(video.currentTime);
    if (!video.paused && !video.ended) {
      frame = requestAnimationFrame(tick);
    }
  };
  const onPlay = (): void => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(tick);
  };
  const onUpdate = (): void => {
    store.set(video.currentTime);
  };
  video.addEventListener("play", onPlay);
  video.addEventListener("seeked", onUpdate);
  video.addEventListener("timeupdate", onUpdate);
  return () => {
    cancelAnimationFrame(frame);
    video.removeEventListener("play", onPlay);
    video.removeEventListener("seeked", onUpdate);
    video.removeEventListener("timeupdate", onUpdate);
  };
}
