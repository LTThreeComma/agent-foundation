import type { Client, ThreadDelta } from "../../service-client";
import { data, type Schema } from "../../shared/api";
import { compareCursors, isObject } from "./projection";

/**
 * One fact the Item projection and the execution view fold, at its
 * `"{attempt}-{sequence}"` stream position. Live deltas and the committed
 * display both become these events, so the folds never know which they read.
 */
export interface RunEvent {
  cursor: string;
  event: {
    event_id: string;
    /** `agui.<lowercased AG-UI type>`, or a Run lifecycle fact. */
    event_type: string;
    run_attempt_id: string | null;
    harness_run_id: string | null;
    item_id: string | null;
    /** Null for a live event that carried no time. */
    occurred_at: string | null;
    payload: Record<string, unknown>;
  };
}

/** A Run's attempts by number: the identity every event of an attempt carries. */
export type Attempts = ReadonlyMap<number, Schema["AttemptView"]>;

/** The whole committed display of a Run, with the Run it describes. */
export function readDisplay(
  client: Client,
  workspaceId: string,
  runId: string,
  signal: AbortSignal,
) {
  return client.http
    .GET("/api/v1/workspaces/{workspace_id}/runs/{run_id}/items", {
      params: { path: { workspace_id: workspaceId, run_id: runId } },
      signal,
    })
    .then(data);
}

/** A transport fragment of a large CUSTOM event; the display keeps its observation. */
export function isFragment(delta: ThreadDelta) {
  return (
    delta.event.type === "CUSTOM" && delta.event.name === "a13n.stream.fragment"
  );
}

function identity(attempts: Attempts, cursor: string) {
  const attempt = attempts.get(Number(cursor.split("-")[0]));
  return {
    run_attempt_id: attempt?.id ?? null,
    harness_run_id: attempt?.harness_run_id ?? null,
  };
}

/** The one translation of a live delta into the event the folds consume. */
export function deltaEvent(delta: ThreadDelta, attempts: Attempts): RunEvent {
  const { type, timestamp, ...fields } = delta.event;
  const cursor = `${delta.attempt}-${delta.sequence}`;
  // Observations are execution facts, not presented Items.
  const item = delta.item?.kind === "observation" ? null : delta.item;
  return {
    cursor,
    event: {
      event_id: `${delta.run_id}:${cursor}`,
      event_type: `agui.${type.toLowerCase()}`,
      ...identity(attempts, cursor),
      item_id: item?.id ?? null,
      occurred_at:
        typeof timestamp === "number"
          ? new Date(timestamp).toISOString()
          : null,
      payload: {
        ...fields,
        ...(item ? { item_kind: item.kind, item_state: item.state } : {}),
      },
    },
  };
}

/** Fields the display copied from the AG-UI event that opened an Item. */
const OPENING_FIELDS = [
  "messageId",
  "role",
  "toolCallId",
  "toolCallName",
  "parentMessageId",
  "metadata",
];

/**
 * The committed display as the events the folds consume, in stream order:
 * each observation is the CUSTOM event it recorded, and each message or tool
 * call is the event that opened it and, once finished, the event that last
 * changed it, each at the time the display recorded for it. The Run's later
 * attempts and its seal are its lifecycle facts. Folding these rebuilds the
 * execution view; the Items themselves are then read from the display.
 */
export function displayEvents(
  read: Schema["RunItems"],
  attempts: Attempts,
): RunEvent[] {
  const events = new Map<string, RunEvent>();
  const put = (
    cursor: string,
    event: Pick<RunEvent["event"], "event_id" | "event_type" | "payload">,
    item: Schema["Item"] | null,
    occurredAt: string | null,
  ) =>
    events.set(cursor, {
      cursor,
      event: {
        ...event,
        ...identity(attempts, cursor),
        item_id: item?.id ?? null,
        occurred_at: occurredAt,
      },
    });
  for (const item of read.items) {
    if (item.kind !== "observation") continue;
    put(
      item.first_stream_id,
      { event_id: item.id, event_type: "agui.custom", payload: item.content },
      null,
      item.started_at,
    );
  }
  for (const item of read.items) {
    if (item.kind === "observation") continue;
    const { content } = item;
    const once = item.first_stream_id === item.last_stream_id;
    put(
      item.first_stream_id,
      {
        event_id: `${item.id}:start`,
        event_type: `agui.${item.kind}_start`,
        payload: {
          ...Object.fromEntries(
            OPENING_FIELDS.filter((field) => field in content).map((field) => [
              field,
              content[field],
            ]),
          ),
          item_kind: item.kind,
          item_state: once ? item.state : "in_progress",
        },
      },
      item,
      item.started_at,
    );
    if (once || item.state === "in_progress" || item.state === "interrupted")
      continue;
    const ending = { item_kind: item.kind, item_state: item.state };
    // A failed call ends on the Harness event that reported the failure.
    const shared = events.get(item.last_stream_id);
    if (shared) {
      shared.event.item_id = item.id;
      shared.event.payload = { ...shared.event.payload, ...ending };
      continue;
    }
    put(
      item.last_stream_id,
      {
        event_id: `${item.id}:end`,
        event_type:
          item.kind !== "tool_call"
            ? `agui.${item.kind}_end`
            : "result" in content
              ? "agui.tool_call_result"
              : "agui.tool_call_end",
        payload: { ...ending, content: content.result },
      },
      item,
      item.ended_at ?? null,
    );
  }
  for (const attempt of attempts.values())
    if (attempt.number > 1)
      put(
        `${attempt.number}-0`,
        {
          event_id: attempt.id,
          event_type: "run_attempt.leased",
          payload: {
            data: {
              attempt_number: attempt.number,
              start_reason: attempt.start_reason,
            },
          },
        },
        null,
        attempt.started_at ?? attempt.created_at,
      );
  const { run, position } = read;
  if (
    run.sealed_at &&
    position &&
    ["completed", "failed", "cancelled"].includes(run.status)
  ) {
    // A sealed display is final: its seal follows everything it covers.
    const [attempt, sequence] = position.split("-").map(Number);
    put(
      `${attempt}-${sequence! + 1}`,
      {
        event_id: `${run.id}:${run.status}`,
        event_type: `run.${run.status}`,
        payload: { data: { failure: run.failure } },
      },
      null,
      run.sealed_at,
    );
  }
  return [...events.values()].sort((a, b) =>
    compareCursors(a.cursor, b.cursor),
  );
}

/** The display dropped the content of its oldest Items to stay within its limit. */
export function isOmitted(content: unknown) {
  return (
    isObject(content) &&
    content.omitted === true &&
    Object.keys(content).length === 1
  );
}
