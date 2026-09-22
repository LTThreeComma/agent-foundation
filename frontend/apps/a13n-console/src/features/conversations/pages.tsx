import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data } from "../../service-client";
import { Page } from "../../shared/page";
import {
  ErrorNotice,
  ErrorPage,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Composer } from "./composer";
import { RunConversation } from "./run";
import styles from "./conversations.module.css";

export function SessionsPage() {
  const { client, path, cache, base, workspace } = useScope();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const page = useCursor();
  const query = useQuery({
    queryKey: [...cache, "sessions", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}/sessions", {
          params: { path, query: { limit: 30, cursor: page.cursor } },
          signal,
        }),
      ),
  });
  return (
    <Page
      title={t("Sessions")}
      description={t("Start a conversation or return to its durable history.")}
      actions={
        workspace.permissions.includes("run") && (
          <Button render={<Link to={`${base}/sessions/new`} />}>
            {t("New conversation")}
          </Button>
        )
      }
    >
      <ErrorNotice error={query.error} />
      {!query.data && !query.error && <Loading />}
      <ResourceTable
        items={query.data?.items ?? []}
        columns={[
          {
            label: t("Session"),
            render: (item) => item.labels.title || t("Conversation"),
          },
          {
            label: t("Created"),
            render: (item) => <Timestamp value={item.created_at} />,
          },
        ]}
        onRowActivate={(item) => navigate(`${base}/sessions/${item.id}`)}
      />
      {query.data?.items.length === 0 && (
        <div className={styles.empty}>
          <h2>{t("Your conversations start here")}</h2>
          <p>{t("Choose an agent and send a message to create a session.")}</p>
        </div>
      )}
      <Pagination page={page} next={query.data?.next_cursor} />
    </Page>
  );
}
export function SessionPage() {
  const { sessionId = "" } = useParams();
  const { client, path, cache, base, workspace } = useScope();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const page = useCursor();
  const query = useQuery({
    queryKey: [...cache, "threads", sessionId, page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}/threads", {
          params: {
            path,
            query: { session_id: sessionId, limit: 30, cursor: page.cursor },
          },
          signal,
        }),
      ),
  });
  return (
    <Page
      title={t("Session")}
      back={`${base}/sessions`}
      description={t("Conversations in this session.")}
      actions={
        workspace.permissions.includes("run") && (
          <Button
            render={<Link to={`${base}/sessions/new?session=${sessionId}`} />}
          >
            {t("New thread")}
          </Button>
        )
      }
    >
      <ErrorNotice error={query.error} />
      <ResourceTable
        items={query.data?.items ?? []}
        columns={[
          {
            label: t("Thread"),
            render: (item) => item.labels.title || t("Conversation"),
          },
          {
            label: t("Status"),
            render: (item) => (
              <StatePill state={item.current_run_id ? "running" : "idle"} />
            ),
          },
          {
            label: t("Created"),
            render: (item) => <Timestamp value={item.created_at} />,
          },
        ]}
        onRowActivate={(item) => navigate(`${base}/threads/${item.id}`)}
      />
      <Pagination page={page} next={query.data?.next_cursor} />
    </Page>
  );
}
export function ConversationPage() {
  const { threadId } = useParams();
  return <Conversation key={threadId ?? "new"} threadId={threadId} />;
}
function Conversation({ threadId }: { threadId?: string }) {
  const { client, path, cache, base } = useScope();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const queries = useQueryClient();
  const [chosenRun, setChosenRun] = useState<string>();
  const runsPage = useCursor();
  const inboxPage = useCursor();
  const thread = useQuery({
    queryKey: [...cache, "thread", threadId],
    enabled: !!threadId,
    refetchInterval: 2000,
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/threads/{identity}",
          { params: { path: { ...path, identity: threadId ?? "" } }, signal },
        ),
      ),
  });
  const runs = useQuery({
    queryKey: [...cache, "runs", threadId, runsPage.cursor],
    enabled: !!threadId,
    refetchInterval: 2000,
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/runs",
          {
            params: {
              path: { ...path, thread_id: threadId ?? "" },
              query: { limit: 20, cursor: runsPage.cursor },
            },
            signal,
          },
        ),
      ),
  });
  const inbox = useQuery({
    queryKey: [...cache, "inbox", threadId, inboxPage.cursor],
    enabled: !!threadId,
    refetchInterval: 2000,
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox",
          {
            params: {
              path: { ...path, thread_id: threadId ?? "" },
              query: { limit: 20, cursor: inboxPage.cursor },
            },
            signal,
          },
        ),
      ),
  });
  const [identity, workspaceId] = cache;
  const refresh = useCallback(() => {
    void queries.invalidateQueries({ queryKey: [identity, workspaceId] });
  }, [queries, identity, workspaceId]);
  const selectedRun =
    chosenRun ??
    thread.data?.current_run_id ??
    thread.data?.last_run_id ??
    runs.data?.items[0]?.id;
  if (threadId && thread.error && !thread.data)
    return <ErrorPage error={thread.error} />;
  if (threadId && !thread.data) return <Loading />;
  const options =
    runs.data?.items.map((item) => ({
      value: item.id,
      label: `${new Date(item.created_at).toLocaleString()} · ${t(item.status)}`,
    })) ?? [];
  if (selectedRun && !options.some((item) => item.value === selectedRun))
    options.unshift({ value: selectedRun, label: t("Selected run") });
  return (
    <Page
      title={t(threadId ? "Conversation" : "New conversation")}
      back={
        thread.data
          ? `${base}/sessions/${thread.data.session_id}`
          : `${base}/sessions`
      }
    >
      <div className={styles.conversation}>
        <ErrorNotice error={thread.error ?? runs.error} />
        {threadId && (
          <>
            <ChoiceField
              label={t("Run history")}
              value={selectedRun}
              options={options}
              onValueChange={setChosenRun}
            />
            <Pagination page={runsPage} next={runs.data?.next_cursor} />
          </>
        )}
        {selectedRun ? (
          <RunConversation
            key={selectedRun}
            runId={selectedRun}
            onUpdate={refresh}
          />
        ) : (
          <div className={styles.empty}>
            <h2>{t("What would you like to work on?")}</h2>
            <p>
              {t(
                "Your agent uses its current default revision when the message is accepted.",
              )}
            </p>
          </div>
        )}
        {threadId && (
          <details className={styles.inbox}>
            <summary>{t("Inbox and delivery status")}</summary>
            <ErrorNotice error={inbox.error} />
            {inbox.data?.items.map((item) => (
              <div key={item.id} className={styles.inboxRow}>
                <span>
                  {item.payload?.content
                    .map((part) => part.text)
                    .join(" ")
                    .slice(0, 160) || t(item.kind)}
                </span>
                <StatePill state={item.status} />
                <small>{t(item.delivery)}</small>
              </div>
            ))}
            <Pagination page={inboxPage} next={inbox.data?.next_cursor} />
          </details>
        )}
        <Composer
          threadId={threadId}
          sessionId={search.get("session") ?? undefined}
          currentRunId={thread.data?.current_run_id}
          onInterrupted={refresh}
          onSubmitted={(result) => {
            refresh();
            if (!threadId)
              navigate(`${base}/threads/${result.thread_id}`, {
                replace: true,
              });
            else setChosenRun(undefined);
          }}
        />
      </div>
    </Page>
  );
}
