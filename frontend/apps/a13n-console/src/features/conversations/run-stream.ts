import type { ThreadDelta } from "../../service-client";
import { isCancelledError, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries, invalidateConversation } from "./api";
import {
  deltaEvent,
  displayEvents,
  isFragment,
  isOmitted,
  type Attempts,
  type RunEvent,
} from "./display";
import {
  compareCursors,
  mergeRetainedItems,
  type PresentedItem,
} from "./projection";
import {
  applyRun,
  emptyExecution,
  type Execution,
  type ExecutionCoverage,
  type RunFold,
} from "./execution";

export type { ExecutionCoverage };

export interface RunExecution extends Execution {
  coverage: ExecutionCoverage;
}

/** How long an active Run may go unconfirmed while its Thread reports no change. */
const SEAL_CHECK_MS = 10_000;

/**
 * One consumer per Run. The committed display is folded as the events it
 * recorded, so the Item projection and the execution view can never disagree,
 * and the Thread stream's deltas continue both after the display's position.
 *
 * `live` follows the Thread stream while the page shows this Run: the Run's
 * own output while it is active, and the Thread's changes at any time. An
 * attempt reset discards provisional output; a gap re-reads the display and
 * heals at the next boundary, whose display covers what the stream skipped.
 */
export function useRunStream(
  runId: string,
  { live = false }: { live?: boolean } = {},
) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    identity = useRef("");
  const [items, setItems] = useState<PresentedItem[]>([]),
    [execution, setExecution] = useState<RunExecution>(() => ({
      ...emptyExecution(),
      coverage: "partial",
    })),
    [state, setState] = useState<
      "connecting" | "connected" | "closed" | "disconnected"
    >("connecting"),
    [gap, setGap] = useState(false),
    [incomplete, setIncomplete] = useState(false),
    [dropped, setDropped] = useState(0),
    [error, setError] = useState<unknown>(),
    [generation, setGeneration] = useState(0);
  useEffect(() => {
    const controller = new AbortController(),
      { signal } = controller,
      queries = conversationQueries(client, workspace.id);
    const selected = `${workspace.id}:${runId}`;
    if (identity.current !== selected) {
      identity.current = selected;
      setItems([]);
      setGap(false);
      setIncomplete(false);
      setDropped(0);
    }
    let read: Schema["RunItems"] | undefined;
    let attempts: Attempts = new Map();
    let fold: RunFold = { items: new Map(), execution: emptyExecution() };
    // Deltas beyond the display's position.
    let provisional: RunEvent[] = [];
    const asked = new Set<number>();
    // The display omitted content, or the stream skipped deltas not yet covered.
    let omitted = false;
    let skipped = false;
    // What the stream could not supply waits for the next boundary's display.
    let stale = false;
    let frame: number | undefined;
    function publish() {
      if (frame !== undefined) return;
      frame = requestAnimationFrame(() => {
        frame = undefined;
        if (signal.aborted) return;
        setItems(sorted(fold.items));
        setExecution({
          ...fold.execution,
          // Items the display dropped took their execution facts with them.
          coverage:
            omitted || skipped || !!read?.dropped ? "partial" : "complete",
        });
      });
    }
    async function current<T>(read: () => Promise<T>): Promise<T> {
      for (;;) {
        signal.throwIfAborted();
        try {
          return await read();
        } catch (error) {
          // Thread changes may replace a shared query while recovery awaits it.
          // Join its replacement instead of reporting a disconnected stream.
          if (!isCancelledError(error) || signal.aborted) throw error;
        }
      }
    }
    /** Read the display again and fold it with the output it does not cover yet. */
    async function reconcile(discard = false) {
      const [next, list] = await Promise.all([
        current(() =>
          cache.fetchQuery({ ...queries.items(runId), staleTime: 0 }),
        ),
        current(() =>
          cache.fetchQuery({ ...queries.attempts(runId), staleTime: 0 }),
        ),
      ]);
      signal.throwIfAborted();
      read = next;
      attempts = new Map(list.map((attempt) => [attempt.number, attempt]));
      cache.setQueryData(queries.run(runId).queryKey, next.run);
      const { position } = next;
      provisional = discard
        ? []
        : provisional.filter(
            (entry) => !position || compareCursors(entry.cursor, position) > 0,
          );
      fold = { items: new Map(), execution: emptyExecution() };
      for (const entry of displayEvents(next, attempts))
        fold = applyRun(fold, entry);
      // The display is authoritative for Items.
      fold = {
        ...fold,
        items: mergeRetainedItems(
          fold.items,
          next.items.filter((item) => item.kind !== "observation"),
        ),
      };
      for (const entry of provisional) fold = applyRun(fold, entry);
      omitted = next.items.some((item) => isOmitted(item.content));
      setIncomplete(omitted);
      setGap(omitted || skipped);
      setDropped(next.dropped);
      publish();
      return next;
    }
    function covered(cursor: string) {
      const last = provisional.at(-1)?.cursor ?? read?.position;
      return !!last && compareCursors(cursor, last) <= 0;
    }
    async function receive(delta: ThreadDelta) {
      const cursor = `${delta.attempt}-${delta.sequence}`;
      if (covered(cursor)) return;
      if (!attempts.has(delta.attempt) && !asked.has(delta.attempt)) {
        // A new attempt: learn its identity, and the Run's status, first.
        asked.add(delta.attempt);
        await reconcile();
        if (covered(cursor)) return;
      }
      if (isFragment(delta)) {
        // Only the display holds a large observation, from the next boundary.
        if (delta.item) stale = true;
        return;
      }
      const entry = deltaEvent(delta, attempts);
      provisional.push(entry);
      fold = applyRun(fold, entry);
      publish();
    }
    async function follow(threadId: string) {
      // The Thread may have moved on between the display read and the stream
      // attachment; its Run reports a seal the stream would never announce.
      const check = setInterval(() => {
        if (!read || read.complete) return;
        void current(() =>
          cache.fetchQuery({ ...queries.run(runId), staleTime: 0 }),
        )
          .then((run) => (run.sealed_at ? reconcile() : undefined))
          .catch(() => undefined);
      }, SEAL_CHECK_MS);
      try {
        for await (const next of client.streamThread(workspace.id, threadId, {
          signal,
        })) {
          if (signal.aborted) return;
          if (next.type === "changed") {
            void invalidateConversation(cache, workspace.id, {
              sessionId: read?.run.session_id,
              threadId,
            });
            // Sealing this Run is one of the changes a Thread reports.
            if (!read?.complete) {
              const thread = await current(() =>
                cache.fetchQuery({ ...queries.thread(threadId), staleTime: 0 }),
              );
              if (thread.current_run_id !== runId) await reconcile();
            }
          } else if (next.type === "delta") {
            if (next.delta.run_id === runId) await receive(next.delta);
          } else if (next.run_id !== runId) continue;
          else if (next.type === "reset") {
            stale = true;
            await reconcile(true);
          } else if (next.type === "gap") {
            skipped = stale = true;
            await reconcile();
          } else if (stale) {
            skipped = stale = false;
            await reconcile();
          }
          setState(read?.complete ? "closed" : "connected");
        }
      } finally {
        clearInterval(check);
      }
    }
    async function attach() {
      setState("connecting");
      setError(undefined);
      const first = await reconcile(true);
      setState(first.complete ? "closed" : "connecting");
      if (!live) return;
      await follow(first.run.thread_id);
      if (!signal.aborted) setState("disconnected");
    }
    void attach().catch((error) => {
      if (!signal.aborted) {
        revalidateSession(error);
        setError(error);
        setState("disconnected");
      }
    });
    return () => {
      controller.abort();
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [client, workspace.id, runId, cache, generation, live]);
  return {
    items,
    execution,
    state,
    gap,
    incomplete,
    /** The earliest Items the display dropped over its item limit. */
    dropped,
    error,
    reconnect: () => setGeneration((value) => value + 1),
  };
}

function sorted(items: ReadonlyMap<string, PresentedItem>) {
  return [...items.values()].sort((a, b) =>
    compareCursors(a.firstCursor, b.firstCursor),
  );
}
