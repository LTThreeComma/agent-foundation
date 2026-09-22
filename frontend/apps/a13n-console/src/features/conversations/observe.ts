import {
  ApiError,
  data,
  ProtocolError,
  type Client,
  type components,
} from "../../service-client";
import { isRecord } from "../../service-client/errors";
import { decodeSse } from "../../service-client/streams/sse";
import { delay } from "../../service-client/transport";

export type RunItems = components["schemas"]["RunItems"];
export type LiveText = { id: string; type: "text" | "reasoning"; text: string };
export type Observation = {
  snapshot: RunItems;
  live: LiveText[];
  connection: "live" | "recovering" | "complete";
};

/** Temporary text suffix only; SQL lifecycle and durable attempt segments own all history. */
export function appendText(
  messages: LiveText[],
  event: Record<string, unknown>,
): LiveText[] {
  if (isRecord(event.metadata) && event.metadata.display === false)
    return messages;
  if (
    event.type !== "TEXT_MESSAGE_CONTENT" &&
    event.type !== "REASONING_MESSAGE_CONTENT"
  )
    return messages;
  if (typeof event.messageId !== "string" || typeof event.delta !== "string")
    throw new ProtocolError("Invalid text event.");
  const type = event.type === "TEXT_MESSAGE_CONTENT" ? "text" : "reasoning";
  const prior = messages.find(
    (item) => item.id === event.messageId && item.type === type,
  );
  return prior
    ? messages.map((item) =>
        item === prior ? { ...item, text: item.text + event.delta } : item,
      )
    : [...messages, { id: event.messageId, type, text: event.delta }];
}

type Control = {
  display_version: string;
  cursor: string | null;
  retry_after_ms: number;
};
function controlPayload(text: string): Control {
  const value: unknown = JSON.parse(text);
  if (
    !isRecord(value) ||
    typeof value.display_version !== "string" ||
    !value.display_version ||
    !(
      value.cursor === null ||
      (typeof value.cursor === "string" && !!value.cursor)
    ) ||
    typeof value.retry_after_ms !== "number" ||
    !Number.isSafeInteger(value.retry_after_ms) ||
    value.retry_after_ms < 0 ||
    value.retry_after_ms > 2_147_483_647
  )
    throw new ProtocolError("Invalid run stream control.");
  return {
    display_version: value.display_version,
    cursor: value.cursor,
    retry_after_ms: value.retry_after_ms,
  };
}

export async function observeRun(
  client: Client,
  workspaceId: string,
  runId: string,
  inputCursor: string | undefined,
  signal: AbortSignal,
  publish: (value: Observation) => void,
): Promise<void> {
  const path = { workspace_id: workspaceId, run_id: runId };
  let latest: RunItems | undefined;
  let failures = 0;
  let received: { attempt: string; sequence: number } | undefined;
  let required: { control: Control; observedVersion: string } | undefined;
  while (true) {
    signal.throwIfAborted();
    let progressed = false;
    let retryAfter = Math.max(250, required?.control.retry_after_ms ?? 0);
    let failure: unknown = new ProtocolError(
      "Live history is not advancing. Reconnect to refresh durable history.",
    );
    try {
      const previousVersion = latest?.display_version;
      // Every reconnect starts from a fresh durable boundary, never a transient suffix or control cursor.
      latest = data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/runs/{run_id}/items",
          {
            params: { path, query: { limit: 50, input_cursor: inputCursor } },
            signal,
          },
        ),
      );
      progressed =
        previousVersion !== undefined &&
        previousVersion !== latest.display_version;
      publish({
        snapshot: latest,
        live: [],
        connection: latest.complete ? "complete" : "recovering",
      });
      if (latest.complete) return;
      if (required) {
        const { control, observedVersion } = required;
        // Versions/cursors are opaque. An unchanged old view cannot satisfy a newer control;
        // a fresh different view may already supersede it and is checked again by the gateway.
        if (
          (latest.display_version === observedVersion &&
            latest.display_version !== control.display_version) ||
          (latest.display_version === control.display_version &&
            latest.cursor !== control.cursor)
        )
          throw new ProtocolError(
            "The required durable history is not available yet. Reconnect to try again.",
          );
        required = undefined;
      }
      const segment = latest.segments.at(-1);
      if (!latest.cursor || !segment)
        throw new ProtocolError(
          "The run has no durable stream boundary yet. Reconnect to refresh history.",
        );
      const url = new URL(
        `/api/v1/workspaces/${workspaceId}/runs/${runId}/events`,
        location.origin,
      );
      url.searchParams.set("cursor", latest.cursor);
      const response = await client.fetch(
        new Request(url, { signal, headers: { Accept: "text/event-stream" } }),
      );
      if (!response.body)
        throw new ProtocolError("The Service returned an empty event stream.");
      let sequence = segment.event_sequence ?? 0;
      let live: LiveText[] = [];
      let characters = 0;
      let count = 0;
      const started = Date.now();
      for await (const frame of decodeSse(response.body)) {
        signal.throwIfAborted();
        if (["reset", "retry_later", "closed"].includes(frame.event)) {
          const control = controlPayload(frame.data);
          required = { control, observedVersion: latest.display_version };
          retryAfter = Math.max(retryAfter, control.retry_after_ms);
          break;
        }
        if (frame.event !== "data")
          throw new ProtocolError("Unknown run stream frame.");
        const value: unknown = JSON.parse(frame.data);
        if (
          !isRecord(value) ||
          !isRecord(value.event) ||
          typeof value.event_sequence !== "number" ||
          !Number.isSafeInteger(value.event_sequence) ||
          value.attempt_number !== segment.attempt_number ||
          value.event_sequence <= sequence
        )
          throw new ProtocolError("Run stream coverage changed.");
        sequence = value.event_sequence; // Hidden source/steer observations intentionally leave sequence gaps.
        characters += frame.data.length;
        count += 1;
        if (characters > 1024 * 1024 || count > 512) break;
        if (
          received?.attempt !== segment.attempt_id ||
          sequence > received.sequence
        ) {
          progressed = true;
          received = { attempt: segment.attempt_id, sequence };
        }
        live = appendText(live, value.event);
        publish({ snapshot: latest, live, connection: "live" });
        if (Date.now() - started > 1000) break;
      }
    } catch (error) {
      if (signal.aborted) throw error;
      if (error instanceof ApiError && [401, 403, 404].includes(error.status))
        throw error;
      failure = error;
    }
    failures = progressed ? 0 : failures + 1;
    if (latest)
      publish({ snapshot: latest, live: [], connection: "recovering" });
    if (failures > 5) throw failure;
    await delay(
      Math.max(
        retryAfter,
        failures ? Math.min(500 * 2 ** failures, 10_000) : 250,
      ),
      signal,
    );
  }
}
