import {
  api,
  ApiError,
  type AskProblem,
  type AskRequest,
  type AskResult,
  type AskStart,
} from "./client";

/** What the question endpoint streams, in this order: « start », « token »…,
 * « checking » (visual check), then « done »; « error » instead when it fails. */
export type AskEvent =
  | { event: "start"; data: AskStart }
  | { event: "token"; data: { text: string } }
  | { event: "checking"; data: { frames: number } }
  | { event: "done"; data: AskResult }
  | { event: "error"; data: AskProblem };

const NAMES = new Set(["start", "token", "checking", "done", "error"]);

interface RawEvent {
  event: string;
  data: string;
}

/** One event block (``event:`` and ``data:`` lines); comments (pings) and ids are ignored. */
function parseBlock(block: string): RawEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r\n|\r|\n/)) {
    if (!line || line.startsWith(":")) {
      continue;
    }
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }
    if (field === "event") {
      event = value;
    } else if (field === "data") {
      data.push(value);
    }
  }
  return data.length > 0 ? { event, data: data.join("\n") } : null;
}

/** Server-Sent Events read from pieces of text, whatever the pieces' boundaries. */
export class SseParser {
  private buffer = "";

  push(text: string): RawEvent[] {
    this.buffer += text;
    const out: RawEvent[] = [];
    for (;;) {
      const end = /\r\n\r\n|\n\n|\r\r/.exec(this.buffer);
      if (!end) {
        return out;
      }
      const block = parseBlock(this.buffer.slice(0, end.index));
      this.buffer = this.buffer.slice(end.index + end[0].length);
      if (block) {
        out.push(block);
      }
    }
  }

  /** What is left when the stream ends (a last event without its blank line). */
  flush(): RawEvent[] {
    const block = parseBlock(this.buffer);
    this.buffer = "";
    return block ? [block] : [];
  }
}

function typed(raw: RawEvent): AskEvent | null {
  if (!NAMES.has(raw.event)) {
    return null;
  }
  try {
    return { event: raw.event, data: JSON.parse(raw.data) as unknown } as AskEvent;
  } catch {
    return null;
  }
}

/**
 * Ask a question and hand each event to ``onEvent`` as it arrives. Aborting ``signal`` (the
 * stop button, leaving the page) closes the connection: the server then stops the model.
 */
export async function streamAsk(
  body: AskRequest,
  { signal, onEvent }: { signal: AbortSignal; onEvent: (event: AskEvent) => void },
): Promise<void> {
  const { data, error, response } = await api.POST("/api/v1/ask", {
    body,
    parseAs: "stream",
    signal,
  });
  if (error !== undefined || !response.ok || !data) {
    throw ApiError.from(error, response.status);
  }
  const reader = data.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const emit = (events: RawEvent[]): void => {
    for (const raw of events) {
      const event = typed(raw);
      if (event) {
        onEvent(event);
      }
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      emit(parser.push(decoder.decode(value, { stream: true })));
    }
    emit(parser.push(decoder.decode()));
    emit(parser.flush());
  } finally {
    reader.releaseLock();
  }
}
