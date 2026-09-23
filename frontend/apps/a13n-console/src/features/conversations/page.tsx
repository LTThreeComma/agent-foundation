import { useQuery } from "@tanstack/react-query";
import { Navigate, Outlet, useLocation, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { conversationQueries } from "./api";
import { SessionHeader } from "./session-header";
import { SessionList } from "./list";
import { RunCollapseProvider } from "./transcript/debug/collapse";
import styles from "./conversations.module.css";

export function ConversationsPage() {
  const { sessionId } = useParams();
  const location = useLocation();
  const nested = !!sessionId || /\/sessions\/new\/?$/.test(location.pathname);
  return nested ? (
    <div className={`${styles.sessionStage} a13n-scrollbar`}>
      <Outlet />
    </div>
  ) : (
    <SessionList />
  );
}

export function SessionLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId } = useParams(),
    { workspace, client } = useScope(),
    queries = conversationQueries(client, workspace.id);
  const threads = useQuery({
    ...queries.threads(sessionId),
    refetchInterval: 2000,
  });
  const summary = useQuery(queries.session(sessionId));
  const selected =
    summary.data?.selected_thread_id ??
    (summary.data ? threads.data?.[0]?.id : undefined);
  // The disclosure level lives in the search string; redirects keep it.
  const { search } = useLocation();
  return (
    // The header and the run sections share one owner for what is collapsed.
    <RunCollapseProvider>
      <div className={styles.sessionDetail}>
        <SessionHeader threads={threads.data ?? []} />
        <ErrorNotice
          error={threads.error ?? summary.error}
          retry={() => {
            void threads.refetch();
            void summary.refetch();
          }}
        />
        <div
          className={`${styles.sessionContent} a13n-scrollbar`}
          data-session-stage
        >
          {threadId ? (
            <Outlet />
          ) : selected ? (
            <Navigate to={`threads/${selected}${search}`} replace />
          ) : threads.isPending || summary.isPending ? (
            <Loading variant="list" rows={3} />
          ) : (
            <Empty
              title={t("No threads yet")}
              description={t(
                "Threads created by the host application appear here.",
              )}
            />
          )}
        </div>
      </div>
    </RunCollapseProvider>
  );
}
