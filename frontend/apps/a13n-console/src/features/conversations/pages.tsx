import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data } from "../../service-client";
import { Page } from "../../shared/page";
import {
  ErrorNotice,
  ErrorPage,
  Loading,
  StatePill,
} from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import { Composer } from "./composer";
import { RunConversation } from "./run";
import { WaitingPanel } from "./waiting";
import { invalidateConversation } from "./api";
import { inputContent } from "./input-content";
import styles from "./conversations.module.css";

export function ConversationPage() {
  const { threadId } = useParams();
  return <Conversation key={threadId ?? "new"} threadId={threadId} />;
}
function Conversation({ threadId }: { threadId?: string }) {
  const { sessionId } = useParams();
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
  const [identity] = cache;
  const workspaceId = path.workspace_id;
  const refresh = useCallback(() => {
    void queries.invalidateQueries({ queryKey: [identity, workspaceId] });
    void invalidateConversation(queries, workspaceId);
  }, [queries, identity, workspaceId]);
  const [responseNotice, setResponseNotice] = useState("");
  const selectedRun =
    chosenRun ??
    thread.data?.current_run_id ??
    thread.data?.last_run_id ??
    runs.data?.items[0]?.id;
  if (threadId && thread.error && !thread.data)
    return <ErrorPage error={thread.error} />;
  if (sessionId && thread.data && thread.data.session_id !== sessionId)
    return (
      <ErrorPage
        error={
          new Error(t("This thread does not belong to this conversation."))
        }
      />
    );
  if (threadId && !thread.data) return <Loading />;
  const options =
    runs.data?.items.map((item) => ({
      value: item.id,
      label: `${new Date(item.created_at).toLocaleString()} · ${t(item.status)}`,
    })) ?? [];
  if (selectedRun && !options.some((item) => item.value === selectedRun))
    options.unshift({ value: selectedRun, label: t("Selected run") });
  const content = (
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
                {item.payload && "content" in item.payload
                  ? item.payload.content
                      .map(inputContent)
                      .join(" ")
                      .slice(0, 160)
                  : t("Control response")}
              </span>
              <StatePill state={item.status} />
              <small>{t(item.delivery)}</small>
            </div>
          ))}
          <Pagination page={inboxPage} next={inbox.data?.next_cursor} />
        </details>
      )}
      {responseNotice && <p role="status">{responseNotice}</p>}
      {threadId && thread.data?.head_run_id && (
        <WaitingPanel
          key={thread.data.head_run_id}
          threadId={threadId}
          runId={thread.data.head_run_id}
          busy={!!thread.data.current_run_id}
          lastRunId={thread.data.last_run_id}
          onSubmitted={(result) => {
            setResponseNotice(
              result.entry.failure?.code === "stale_feedback"
                ? t(
                    "This waiting run is no longer current. Refreshing the conversation.",
                  )
                : result.entry.status === "failed"
                  ? t(
                      "The response could not start a run. Check its inbox status.",
                    )
                  : result.replayed
                    ? t(
                        "Your previous response was found. Its current status is shown in the inbox.",
                      )
                    : t("Response submitted."),
            );
            refresh();
            setChosenRun(undefined);
          }}
        />
      )}
      <Composer
        threadId={threadId}
        sessionId={search.get("session") ?? undefined}
        currentRunId={thread.data?.current_run_id}
        onInterrupted={refresh}
        onSubmitted={(result) => {
          refresh();
          if (!threadId)
            navigate(
              `${base}/sessions/${result.session_id}/threads/${result.thread_id}`,
              {
                replace: true,
              },
            );
          else setChosenRun(undefined);
        }}
      />
    </div>
  );
  if (sessionId) return content;
  return (
    <Page
      title={t(threadId ? "Conversation" : "New conversation")}
      back={
        thread.data
          ? `${base}/sessions/${thread.data.session_id}`
          : `${base}/sessions`
      }
    >
      {content}
    </Page>
  );
}
