import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import createClient from "openapi-fetch";

import type * as ClientModule from "./client";
import type { paths } from "./schema";

import { SseParser, streamAsk, type AskEvent } from "./ask";

vi.mock("./client", async (original) => {
  const actual = await original<typeof ClientModule>();
  const fetch = (request: Request) => globalThis.fetch(request);
  return { ...actual, api: createClient<paths>({ baseUrl: "http://localhost", fetch }) };
});

describe("SseParser", () => {
  it("reads events whatever the boundaries of the pieces", () => {
    const text =
      ": ping - 2026-09-28\r\n\r\n" +
      'event: start\r\ndata: {"id":"q1"}\r\n\r\n' +
      'event: token\r\ndata: {"text":"Un chat"}\r\n\r\n' +
      'event: token\ndata: {"text":" dort [1]."}\n\n' +
      "data: line one\ndata: line two\n\n";
    for (const size of [1, 3, 7, 1000]) {
      const parser = new SseParser();
      const events = [];
      for (let at = 0; at < text.length; at += size) {
        events.push(...parser.push(text.slice(at, at + size)));
      }
      events.push(...parser.flush());
      expect(events).toEqual([
        { event: "start", data: '{"id":"q1"}' },
        { event: "token", data: '{"text":"Un chat"}' },
        { event: "token", data: '{"text":" dort [1]."}' },
        { event: "message", data: "line one\nline two" },
      ]);
    }
  });

  it("keeps a last event without its blank line for the end", () => {
    const parser = new SseParser();
    expect(parser.push('event: done\r\ndata: {"id":"q1"}')).toEqual([]);
    expect(parser.flush()).toEqual([{ event: "done", data: '{"id":"q1"}' }]);
    expect(parser.flush()).toEqual([]);
  });
});

const server = setupServer();
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});
afterEach(() => {
  server.resetHandlers();
});
afterAll(() => {
  server.close();
});

describe("streamAsk", () => {
  it("hands over the typed events as they arrive", async () => {
    server.use(
      http.post("http://localhost/api/v1/ask", () => {
        const encoder = new TextEncoder();
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            for (const chunk of [
              'event: start\r\ndata: {"id":"q1","passages":3}\r\n\r\nevent: tok',
              'en\r\ndata: {"text":"Bonjour"}\r\n\r\n',
              "event: unknown\r\ndata: {}\r\n\r\n",
              'event: done\r\ndata: {"id":"q1","status":"answered"}\r\n\r\n',
            ]) {
              controller.enqueue(encoder.encode(chunk));
            }
            controller.close();
          },
        });
        return new HttpResponse(body, { headers: { "Content-Type": "text/event-stream" } });
      }),
    );
    const events: AskEvent[] = [];
    await streamAsk(
      { question: "riz ?", visual_check: false },
      {
        signal: new AbortController().signal,
        onEvent: (event) => events.push(event),
      },
    );
    expect(events.map((e) => e.event)).toEqual(["start", "token", "done"]);
    expect(events[1]).toEqual({ event: "token", data: { text: "Bonjour" } });
  });

  it("throws the problem of a refused request", async () => {
    server.use(
      http.post("http://localhost/api/v1/ask", () =>
        HttpResponse.json(
          { status: 422, code: "validation_error", detail: "Certains champs sont invalides." },
          { status: 422 },
        ),
      ),
    );
    await expect(
      streamAsk(
        { question: "", visual_check: false },
        { signal: new AbortController().signal, onEvent: vi.fn() },
      ),
    ).rejects.toMatchObject({ status: 422, code: "validation_error" });
  });
});
