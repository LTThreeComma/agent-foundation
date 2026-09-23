import { data, type Client, type paths } from "../../service-client";
import { allPages } from "../../shared/api";
import type { QueryClient } from "@tanstack/react-query";

export type ViewLevel = "chat" | "debug";

export function isActiveRun(status: string) {
  return status === "accepted" || status === "running";
}

export async function invalidateConversation(
  cache: QueryClient,
  workspaceId: string,
) {
  await Promise.all(
    ["sessions", "session", "session-threads", "thread", "run"].map((kind) =>
      cache.invalidateQueries({ queryKey: [kind, workspaceId] }),
    ),
  );
}

export type SessionFilters = Omit<
  NonNullable<
    paths["/api/v1/workspaces/{workspace_id}/sessions"]["get"]["parameters"]["query"]
  >,
  "limit" | "cursor"
>;

export function conversationQueries(client: Client, workspaceId: string) {
  return {
    session: (sessionId: string) => ({
      queryKey: ["session", workspaceId, sessionId],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        data(
          await client.http.GET(
            "/api/v1/workspaces/{workspace_id}/sessions/{identity}",
            {
              params: {
                path: { workspace_id: workspaceId, identity: sessionId },
              },
              signal,
            },
          ),
        ),
    }),
    threads: (sessionId: string) => ({
      queryKey: ["session-threads", workspaceId, sessionId],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        allPages(async (cursor) =>
          data(
            await client.http.GET("/api/v1/workspaces/{workspace_id}/threads", {
              params: {
                path: { workspace_id: workspaceId },
                query: { session_id: sessionId, cursor, limit: 100 },
              },
              signal,
            }),
          ),
        ),
    }),
    thread: (threadId: string) => ({
      queryKey: ["thread", workspaceId, threadId],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        data(
          await client.http.GET(
            "/api/v1/workspaces/{workspace_id}/threads/{identity}",
            {
              params: {
                path: { workspace_id: workspaceId, identity: threadId },
              },
              signal,
            },
          ),
        ),
    }),
    run: (runId: string) => ({
      queryKey: ["run", workspaceId, runId],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        data(
          await client.http.GET(
            "/api/v1/workspaces/{workspace_id}/runs/{run_id}",
            {
              params: { path: { workspace_id: workspaceId, run_id: runId } },
              signal,
            },
          ),
        ),
    }),
    sessions: (cursor?: string, filters: SessionFilters = {}) => ({
      queryKey: ["sessions", workspaceId, cursor, filters],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        data(
          await client.http.GET("/api/v1/workspaces/{workspace_id}/sessions", {
            params: {
              path: { workspace_id: workspaceId },
              query: { ...filters, cursor, limit: 30 },
            },
            signal,
          }),
        ),
    }),
  };
}
