import { expect, it, vi } from "vitest";
import { createClient, type ThreadDelta } from "../../service-client";
import type { Schema } from "../../shared/api";
import { fixtureRun } from "./transcript/fixture";
import {
  deltaEvent,
  displayEvents,
  isFragment,
  readDisplay,
  type Attempts,
} from "./display";
import { applyRun, emptyExecution, type RunFold } from "./execution";

const attempt = (number: number): Schema["AttemptView"] => ({
  id: `att_${number}`,
  run_id: "run_2",
  number,
  status: "succeeded",
  start_reason: number === 1 ? "initial" : "recovery",
  yield_reason: null,
  failure: null,
  harness_run_id: `harness_${number}`,
  worker_build: "build",
  replaces_attempt_id: null,
  started_at: `2026-09-20T10:00:0${number}.000Z`,
  finished_at: null,
  created_at: `2026-09-20T10:00:0${number}.000Z`,
});
const attempts: Attempts = new Map([
  [1, attempt(1)],
  [2, attempt(2)],
]);

const item = (fields: Partial<Schema["Item"]>): Schema["Item"] => ({
  id: "item",
  kind: "text_message",
  state: "completed",
  first_stream_id: "1-1",
  last_stream_id: "1-1",
  started_at: "2026-09-20T10:00:01.000Z",
  content: {},
  ...fields,
});

it("reads the committed display of one Run", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    expect(new URL((input as Request).url).pathname).toBe(
      "/api/v1/workspaces/ws_1/runs/run_2/items",
    );
    return Response.json({
      run: fixtureRun(),
      items: [],
      position: "1-4",
      complete: true,
      dropped: 0,
    });
  });
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch,
  });
  const display = await readDisplay(
    client,
    "ws_1",
    "run_2",
    new AbortController().signal,
  );
  expect(display).toMatchObject({ position: "1-4", complete: true });
});

it("replays the display as the events that opened and closed its Items, at their times", () => {
  const events = displayEvents(
    {
      run: fixtureRun({
        status: "failed",
        failure: { code: "tool_failed", message: "The tool failed." },
      }),
      position: "2-4",
      complete: true,
      dropped: 0,
      items: [
        item({
          id: "msg_1",
          first_stream_id: "1-1",
          last_stream_id: "1-3",
          started_at: "2026-09-20T10:00:01.500Z",
          ended_at: "2026-09-20T10:00:01.900Z",
          content: {
            messageId: "msg_1",
            role: "assistant",
            text: "Reading the file",
          },
        }),
        item({
          id: "call_1",
          kind: "tool_call",
          state: "failed",
          first_stream_id: "2-1",
          last_stream_id: "2-3",
          started_at: "2026-09-20T10:00:03.000Z",
          ended_at: "2026-09-20T10:00:07.000Z",
          content: {
            toolCallId: "call_1",
            toolCallName: "read_file",
            failure: { code: "tool_failed" },
          },
        }),
        item({
          id: "obs_1",
          kind: "observation",
          first_stream_id: "2-3",
          last_stream_id: "2-3",
          started_at: "2026-09-20T10:00:07.000Z",
          ended_at: "2026-09-20T10:00:07.000Z",
          content: { name: "a13n.harness.tool_failed", value: {} },
        }),
      ],
    },
    attempts,
  );
  expect(
    events.map(({ cursor, event }) => [
      cursor,
      event.event_type,
      event.item_id,
      event.harness_run_id,
      event.occurred_at,
    ]),
  ).toEqual([
    [
      "1-1",
      "agui.text_message_start",
      "msg_1",
      "harness_1",
      "2026-09-20T10:00:01.500Z",
    ],
    [
      "1-3",
      "agui.text_message_end",
      "msg_1",
      "harness_1",
      "2026-09-20T10:00:01.900Z",
    ],
    [
      "2-0",
      "run_attempt.leased",
      null,
      "harness_2",
      "2026-09-20T10:00:02.000Z",
    ],
    [
      "2-1",
      "agui.tool_call_start",
      "call_1",
      "harness_2",
      "2026-09-20T10:00:03.000Z",
    ],
    // The failed call ends on the observation that reported it.
    ["2-3", "agui.custom", "call_1", "harness_2", "2026-09-20T10:00:07.000Z"],
    ["2-5", "run.failed", null, "harness_2", fixtureRun().sealed_at],
  ]);
  expect(events[0]!.event.payload).toEqual({
    messageId: "msg_1",
    role: "assistant",
    item_kind: "text_message",
    item_state: "in_progress",
  });
  expect(events[4]!.event.payload).toMatchObject({
    name: "a13n.harness.tool_failed",
    item_kind: "tool_call",
    item_state: "failed",
  });
});

it("leaves an unfinished Item open", () => {
  const events = displayEvents(
    {
      run: fixtureRun({ status: "running", sealed_at: null }),
      position: "1-2",
      complete: false,
      dropped: 0,
      items: [
        item({
          id: "msg_1",
          state: "in_progress",
          first_stream_id: "1-1",
          last_stream_id: "1-2",
        }),
      ],
    },
    new Map([[1, attempt(1)]]),
  );
  expect(events.map((entry) => entry.event.event_type)).toEqual([
    "agui.text_message_start",
  ]);
});

const delta = (fields: Partial<ThreadDelta>): ThreadDelta => ({
  run_id: "run_2",
  attempt: 2,
  sequence: 7,
  event: { type: "TEXT_MESSAGE_CONTENT", messageId: "msg_1", delta: "Hi" },
  item: { id: "msg_1", kind: "text_message", state: "in_progress" },
  ...fields,
});

it("translates a live delta into the event the folds consume", () => {
  expect(
    deltaEvent(
      delta({
        event: {
          type: "TEXT_MESSAGE_CONTENT",
          messageId: "msg_1",
          delta: "Hi",
          timestamp: Date.parse("2026-09-20T10:00:03.000Z"),
        },
      }),
      attempts,
    ),
  ).toEqual({
    cursor: "2-7",
    event: {
      event_id: "run_2:2-7",
      event_type: "agui.text_message_content",
      run_attempt_id: "att_2",
      harness_run_id: "harness_2",
      item_id: "msg_1",
      occurred_at: "2026-09-20T10:00:03.000Z",
      payload: {
        messageId: "msg_1",
        delta: "Hi",
        item_kind: "text_message",
        item_state: "in_progress",
      },
    },
  });
  // An observation is an execution fact, never a presented Item.
  expect(
    deltaEvent(
      delta({
        event: { type: "CUSTOM", name: "a13n.harness.usage", value: {} },
        item: { id: "obs_1", kind: "observation", state: "completed" },
      }),
      attempts,
    ).event,
  ).toMatchObject({ item_id: null, occurred_at: null });
});

it("recognizes the transport fragments of a large event", () => {
  expect(
    isFragment(
      delta({
        event: { type: "CUSTOM", name: "a13n.stream.fragment", value: {} },
      }),
    ),
  ).toBe(true);
  expect(isFragment(delta({}))).toBe(false);
});

const PART_DELTA = "a13n.pydantic_ai.part_delta";
const start = Date.parse("2026-09-20T10:00:03.000Z");

/** A tool call's streamed argument delta, as the stream protocol reports it. */
function argumentDelta(sequence: number, args: string, index = 1): ThreadDelta {
  const timestamp = start + sequence;
  return delta({
    attempt: 1,
    sequence,
    event: {
      type: "CUSTOM",
      timestamp,
      name: PART_DELTA,
      value: {
        thread_id: "thr_1",
        run_id: "harness_1",
        sequence,
        occurred_at: new Date(timestamp).toISOString(),
        event: {
          index,
          delta: {
            tool_name_delta: null,
            args_delta: args,
            tool_call_id: null,
            part_delta_kind: "tool_call",
          },
          event_kind: "part_delta",
        },
      },
    },
    item: { id: `obs_${sequence}`, kind: "observation", state: "completed" },
  });
}

function observed(events: ReturnType<typeof deltaEvent>[]) {
  const fold = events.reduce<RunFold>(applyRun, {
    items: new Map(),
    execution: emptyExecution(),
  });
  return fold.execution.observations.map(({ name, occurredAt, detail }) => ({
    name,
    occurredAt,
    detail,
  }));
}

it("folds a streamed tool call's argument deltas live as the committed display does", () => {
  const attemptsOne: Attempts = new Map([[1, attempt(1)]]);
  const pieces = Array.from({ length: 300 }, (_, index) => `${index},`);
  const each = pieces.map((piece, index) =>
    deltaEvent(argumentDelta(index + 1, piece), attemptsOne),
  );
  // The Service coalesced the same deltas into two stream events.
  const merged = [
    argumentDelta(1, pieces.slice(0, 200).join("")),
    argumentDelta(2, pieces.slice(200).join("")),
  ].map((entry) => deltaEvent(entry, attemptsOne));
  const whole = argumentDelta(1, pieces.join("")).event;
  const committed = displayEvents(
    {
      run: fixtureRun(),
      position: "1-300",
      complete: false,
      dropped: 0,
      items: [
        item({
          id: "obs_arguments",
          kind: "observation",
          first_stream_id: "1-1",
          last_stream_id: "1-300",
          started_at: new Date(start + 1).toISOString(),
          ended_at: new Date(start + 1).toISOString(),
          content: { name: PART_DELTA, value: whole.value },
        }),
      ],
    },
    attemptsOne,
  );

  expect(observed(each)).toEqual(observed(committed));
  expect(observed(merged)).toEqual(observed(committed));
  expect(observed(each)).toHaveLength(1);
});

it("keeps apart the argument deltas of other parts or separated by other events", () => {
  const attemptsOne: Attempts = new Map([[1, attempt(1)]]);
  const events = [
    argumentDelta(1, "{"),
    argumentDelta(2, "}", 2),
    argumentDelta(4, "{"),
    argumentDelta(5, "}"),
  ].map((entry) => deltaEvent(entry, attemptsOne));

  expect(observed(events).map((entry) => entry.detail)).toMatchObject([
    { index: 1, delta: { args_delta: "{" } },
    { index: 2, delta: { args_delta: "}" } },
    { index: 1, delta: { args_delta: "{}" } },
  ]);
});
