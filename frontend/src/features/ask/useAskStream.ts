import { useCallback, useEffect, useRef, useState } from "react";

import { streamAsk, type AskEvent } from "@/api/ask";
import {
  ApiError,
  errorMessage,
  type AskRequest,
  type AskResult,
  type AskStart,
} from "@/api/client";

/**
 * searching: the passages are being found (nothing received yet); writing: the model writes;
 * checking: it looks at the images of the cited moments; done: the answer is kept; stopped:
 * the user stopped it (what was written is kept as « cancelled »); error: nothing more comes.
 */
export type AskPhase = "idle" | "searching" | "writing" | "checking" | "done" | "stopped" | "error";

export interface AskState {
  phase: AskPhase;
  question: string;
  start: AskStart | null;
  text: string; // the answer as it is written
  frames: number; // images of the visual check
  result: AskResult | null;
  error: { code: string; message: string } | null;
}

const IDLE: AskState = {
  phase: "idle",
  question: "",
  start: null,
  text: "",
  frames: 0,
  result: null,
  error: null,
};

function reduce(state: AskState, event: AskEvent): AskState {
  switch (event.event) {
    case "start":
      return { ...state, phase: "writing", start: event.data };
    case "token":
      return { ...state, phase: "writing", text: state.text + event.data.text };
    case "checking":
      return { ...state, phase: "checking", frames: event.data.frames };
    case "done":
      return { ...state, phase: "done", result: event.data, text: event.data.answer };
    case "error":
      return {
        ...state,
        phase: "error",
        error: { code: event.data.code, message: event.data.detail },
      };
  }
}

/** One question at a time, streamed; leaving the page stops it (the server stops the model). */
export function useAskStream({ onSettled }: { onSettled?: (state: AskState) => void } = {}) {
  const [state, setState] = useState<AskState>(IDLE);
  const controller = useRef<AbortController | null>(null);
  const settled = useRef(onSettled);
  useEffect(() => {
    settled.current = onSettled;
  });

  const ask = useCallback(async (body: AskRequest): Promise<void> => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    let current: AskState = { ...IDLE, phase: "searching", question: body.question };
    setState(current);
    const apply = (next: AskState): void => {
      current = next;
      if (!abort.signal.aborted) {
        setState(next);
      }
    };
    try {
      await streamAsk(body, {
        signal: abort.signal,
        onEvent: (event) => {
          apply(reduce(current, event));
        },
      });
      if (current.phase !== "done" && current.phase !== "error") {
        apply({ ...current, phase: "error", error: { code: "interrupted", message: "" } });
      }
    } catch (error) {
      if (abort.signal.aborted) {
        current = { ...current, phase: "stopped" };
      } else {
        const code = error instanceof ApiError ? error.code : "network";
        apply({ ...current, phase: "error", error: { code, message: errorMessage(error) } });
      }
    } finally {
      if (controller.current === abort) {
        controller.current = null;
      }
    }
    settled.current?.(current);
  }, []);

  /** The stop button: the connection closes, what was written stays on screen. */
  const stop = useCallback((): void => {
    const abort = controller.current;
    if (!abort) {
      return;
    }
    abort.abort();
    controller.current = null;
    setState((previous) =>
      previous.phase === "idle" || previous.phase === "done"
        ? previous
        : { ...previous, phase: "stopped" },
    );
  }, []);

  /** Forget the answer shown (another question opened from the history). */
  const reset = useCallback((): void => {
    controller.current?.abort();
    controller.current = null;
    setState(IDLE);
  }, []);

  useEffect(
    () => () => {
      controller.current?.abort();
    },
    [],
  );

  return { state, ask, stop, reset };
}
