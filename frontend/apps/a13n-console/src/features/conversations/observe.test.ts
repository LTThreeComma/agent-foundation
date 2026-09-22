import { afterEach, expect, test, vi } from "vitest";
import producerTrace from "./fixtures/hidden-input-trace.json";
import { createClient } from "../../service-client";
import { observeRun, type Observation, type RunItems } from "./observe";

function snapshot(sequence: number, complete = false): RunItems {
  return {
    workspace_id: "ws_test",
    run_id: "run_test",
    status: complete ? "completed" : "running",
    current_attempt_id: complete ? null : "rat_test",
    display_version: `display-${sequence}`,
    cursor: `opaque-${sequence}`,
    execution_checkpoint_cut: null,
    inputs: [],
    next_input_cursor: null,
    segments: [
      {
        attempt_id: "rat_test",
        attempt_number: 1,
        event_sequence: sequence,
        items: [{ id: "message", type: "text", text: "Durable" }],
      },
    ],
    output: null,
    failure: null,
    complete,
  };
}
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
const events = (text: string) =>
  new Response(text, { headers: { "Content-Type": "text/event-stream" } });
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test("reset reads a new durable boundary before reconnect; live text never seals the run", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("location", { origin: "https://service.test" });
  const requests: string[] = [];
  const published: Observation[] = [];
  let reads = 0;
  const client = createClient({
    baseUrl: "https://service.test",
    auth: { type: "session" },
    maxReadRetries: 0,
    fetch: async (input) => {
      const request = input instanceof Request ? input : new Request(input);
      requests.push(request.url);
      if (request.url.includes("/items"))
        return json(snapshot(++reads, reads === 3));
      if (reads === 1)
        return events(
          'event: reset\ndata: {"display_version":"display-2","cursor":"opaque-2","retry_after_ms":0}\n\n',
        );
      return events(
        'id: live-2\nevent: data\ndata: {"attempt_number":1,"event_sequence":3,"event":{"type":"TEXT_MESSAGE_CONTENT","messageId":"message","delta":" suffix"}}\n\nevent: closed\ndata: {"display_version":"display-3","cursor":null,"retry_after_ms":0}\n\n',
      );
    },
  });
  const promise = observeRun(
    client,
    "ws_test",
    "run_test",
    undefined,
    new AbortController().signal,
    (value) => published.push(value),
  );
  await vi.runAllTimersAsync();
  await promise;
  expect(
    requests.map((url) => new URL(url).pathname.split("/").at(-1)),
  ).toEqual(["items", "events", "items", "events", "items"]);
  expect(requests[3]).toContain("cursor=opaque-2");
  expect(requests.join()).not.toContain("must-not-use");
  const live = published.find((value) => value.live.length);
  expect(live?.snapshot.complete).toBe(false);
  expect(live?.live[0].text).toBe(" suffix");
  expect(published.at(-1)?.snapshot.complete).toBe(true);
  expect(published.at(-1)?.live).toEqual([]);
});

test("navigation cancels the subscription without sending an interrupt or replaying input", async () => {
  vi.stubGlobal("location", { origin: "https://service.test" });
  const controller = new AbortController();
  const methods: string[] = [];
  const client = createClient({
    baseUrl: "https://service.test",
    auth: { type: "session" },
    maxReadRetries: 0,
    fetch: async (input) => {
      const request = input instanceof Request ? input : new Request(input);
      methods.push(request.method);
      if (request.url.includes("/items")) return json(snapshot(1));
      return events(
        'event: data\ndata: {"attempt_number":1,"event_sequence":2,"event":{"type":"TEXT_MESSAGE_CONTENT","messageId":"message","delta":"live"}}\n\n',
      );
    },
  });
  await expect(
    observeRun(
      client,
      "ws_test",
      "run_test",
      undefined,
      controller.signal,
      (value) => {
        if (value.live.length) controller.abort();
      },
    ),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(methods).toEqual(["GET", "GET"]);
});

function frame(event: string, value: unknown) {
  return `event: ${event}\ndata: ${JSON.stringify(value)}\n\n`;
}
const delta = (sequence: number, attempt = 1) => ({
  attempt_number: attempt,
  event_sequence: sequence,
  event: {
    type: "TEXT_MESSAGE_CONTENT",
    messageId: "message",
    delta: " visible",
  },
});
function fakeClient(fetcher: typeof fetch) {
  vi.stubGlobal("location", { origin: "https://service.test" });
  return createClient({
    baseUrl: "https://service.test",
    auth: { type: "session" },
    maxReadRetries: 0,
    fetch: fetcher,
  });
}

test("visible sequences may skip hidden observations without a false coverage error", async () => {
  vi.useFakeTimers();
  let reads = 0;
  const published: Observation[] = [];
  const client = fakeClient(async (input) =>
    String(input instanceof Request ? input.url : input).includes("/items")
      ? json(snapshot(1, ++reads > 1))
      : events(frame("data", delta(3))),
  );
  const run = observeRun(
    client,
    "ws_test",
    "run_test",
    undefined,
    new AbortController().signal,
    (value) => published.push(value),
  );
  await vi.runAllTimersAsync();
  await run;
  expect(published.some((value) => value.live[0]?.text === " visible")).toBe(
    true,
  );
});

test.each([
  delta(1),
  delta(0),
  delta(2, 2),
  { ...delta(2), event_sequence: "2" },
  { ...delta(2), event: null },
])(
  "invalid event correlation never enters the visible suffix: %j",
  async (value) => {
    vi.useFakeTimers();
    const published: Observation[] = [];
    const client = fakeClient(async (input) =>
      String(input instanceof Request ? input.url : input).includes("/items")
        ? json(snapshot(1))
        : events(frame("data", value)),
    );
    const run = observeRun(
      client,
      "ws_test",
      "run_test",
      undefined,
      new AbortController().signal,
      (item) => published.push(item),
    ).catch((error) => error);
    await vi.runAllTimersAsync();
    expect(await run).toMatchObject({
      message: "Run stream coverage changed.",
    });
    expect(published.every((item) => item.live.length === 0)).toBe(true);
  },
);

test("retry guidance, no-progress exhaustion, and explicit fresh retry", async () => {
  vi.useFakeTimers();
  let reads = 0,
    streams = 0,
    done = false;
  const client = fakeClient(async (input) => {
    if (
      String(input instanceof Request ? input.url : input).includes("/items")
    ) {
      reads++;
      return json(snapshot(1, done));
    }
    streams++;
    return events(
      frame("retry_later", {
        display_version: "display-1",
        cursor: "opaque-1",
        retry_after_ms: 1000,
      }),
    );
  });
  const run = observeRun(
    client,
    "ws_test",
    "run_test",
    undefined,
    new AbortController().signal,
    () => {},
  ).catch((error) => error);
  await vi.advanceTimersByTimeAsync(999);
  expect({ reads, streams }).toEqual({ reads: 1, streams: 1 });
  await vi.runAllTimersAsync();
  expect(await run).toMatchObject({
    message: expect.stringContaining("Reconnect"),
  });
  expect({ reads, streams }).toEqual({ reads: 6, streams: 6 });
  done = true;
  const recovered: Observation[] = [];
  await observeRun(
    client,
    "ws_test",
    "run_test",
    undefined,
    new AbortController().signal,
    (value) => recovered.push(value),
  );
  expect(recovered.at(-1)?.connection).toBe("complete");
  expect(reads).toBe(7);
});

test("a reset requiring newer durable history cannot reuse the stale cursor", async () => {
  vi.useFakeTimers();
  let reads = 0;
  const streams: string[] = [];
  const client = fakeClient(async (input) => {
    const request = input instanceof Request ? input : new Request(input);
    if (request.url.includes("/items")) {
      reads++;
      return json(snapshot(reads < 3 ? 1 : 2, reads > 3));
    }
    streams.push(request.url);
    return events(
      frame("reset", {
        display_version: "display-2",
        cursor: "opaque-2",
        retry_after_ms: 0,
      }),
    );
  });
  const run = observeRun(
    client,
    "ws_test",
    "run_test",
    undefined,
    new AbortController().signal,
    () => {},
  );
  await vi.runAllTimersAsync();
  await run;
  expect(streams).toHaveLength(2);
  expect(streams[1]).toContain("cursor=opaque-2");
});

test("malformed controls fail bounded recovery and abort interrupts a guided delay immediately", async () => {
  vi.useFakeTimers();
  const client = fakeClient(async (input) =>
    String(input instanceof Request ? input.url : input).includes("/items")
      ? json(snapshot(1))
      : events(frame("retry_later", { cursor: null })),
  );
  const malformed = observeRun(
    client,
    "ws_test",
    "run_test",
    undefined,
    new AbortController().signal,
    () => {},
  ).catch((error) => error);
  await vi.runAllTimersAsync();
  expect(await malformed).toMatchObject({
    message: "Invalid run stream control.",
  });
  const controller = new AbortController();
  const abortable = fakeClient(async (input) =>
    String(input instanceof Request ? input.url : input).includes("/items")
      ? json(snapshot(1))
      : events(
          frame("retry_later", {
            display_version: "display-1",
            cursor: "opaque-1",
            retry_after_ms: 5000,
          }),
        ),
  );
  const aborted = observeRun(
    abortable,
    "ws_test",
    "run_test",
    undefined,
    controller.signal,
    () => {},
  ).catch((error) => error);
  await vi.advanceTimersByTimeAsync(1);
  controller.abort();
  expect(await aborted).toMatchObject({ name: "AbortError" });
});

test("real Service producer trace hides source/steer content and crosses its sequence gap", async () => {
  vi.useFakeTimers();
  const episode = producerTrace.episodes[0];
  let sequence = episode.snapshot.segments.at(-1)?.event_sequence ?? 0;
  let gap = false;
  for (const line of episode.frames.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    const value = JSON.parse(line.slice(6));
    if (typeof value.event_sequence === "number") {
      gap ||= value.event_sequence > sequence + 1;
      sequence = value.event_sequence;
    }
  }
  expect(gap).toBe(true);
  expect(episode.frames).not.toContain(producerTrace.source);
  expect(episode.frames).not.toContain(producerTrace.steer);
  expect(producerTrace.final.inputs).toHaveLength(2);
  expect(
    producerTrace.final.inputs.every((input) => input.status === "consumed"),
  ).toBe(true);
  let reads = 0;
  const published: Observation[] = [];
  const client = fakeClient(async (input) =>
    String(input instanceof Request ? input.url : input).includes("/items")
      ? json(++reads === 1 ? episode.snapshot : producerTrace.final)
      : events(episode.frames),
  );
  const run = observeRun(
    client,
    episode.snapshot.workspace_id,
    episode.snapshot.run_id,
    undefined,
    new AbortController().signal,
    (value) => published.push(value),
  );
  await vi.runAllTimersAsync();
  await run;
  expect(
    published.some((value) =>
      value.live.some((message) => message.text.length > 0),
    ),
  ).toBe(true);
  expect(published.at(-1)?.snapshot.complete).toBe(true);
});

test("duplicate or backward frames after valid data never append a second suffix", async () => {
  vi.useFakeTimers();
  for (const invalid of [3, 2]) {
    const published: Observation[] = [];
    const client = fakeClient(async (input) =>
      String(input instanceof Request ? input.url : input).includes("/items")
        ? json(snapshot(1))
        : events(frame("data", delta(3)) + frame("data", delta(invalid))),
    );
    const run = observeRun(
      client,
      "ws_test",
      "run_test",
      undefined,
      new AbortController().signal,
      (value) => published.push(value),
    ).catch((error) => error);
    await vi.runAllTimersAsync();
    expect(await run).toMatchObject({
      message: "Run stream coverage changed.",
    });
    expect(
      published.every(
        (value) => value.live.length === 0 || value.live[0].text === " visible",
      ),
    ).toBe(true);
  }
});

test("new visible positions keep healthy recovery alive beyond the failure budget", async () => {
  vi.useFakeTimers();
  let reads = 0;
  const published: Observation[] = [];
  const client = fakeClient(async (input) =>
    String(input instanceof Request ? input.url : input).includes("/items")
      ? json(snapshot(1, ++reads === 10))
      : events(
          frame("data", delta(reads + 2)) +
            frame("retry_later", {
              display_version: "display-1",
              cursor: "opaque-1",
              retry_after_ms: 1000,
            }),
        ),
  );
  const run = observeRun(
    client,
    "ws_test",
    "run_test",
    undefined,
    new AbortController().signal,
    (value) => published.push(value),
  );
  await vi.runAllTimersAsync();
  await run;
  expect(reads).toBe(10);
  expect(published.filter((value) => value.connection === "live")).toHaveLength(
    9,
  );
  expect(published.at(-1)?.connection).toBe("complete");
});
