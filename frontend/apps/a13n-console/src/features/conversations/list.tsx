import { Button } from "a13n-ui";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { PlusIcon } from "@phosphor-icons/react";
import { useEffect, useRef } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../auth/context";
import { useScope } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { UNKNOWN } from "../../shared/unknown";
import { Page } from "../../shared/page";
import { AgentAvatar } from "../agents/avatar";
import { conversationQueries, type SessionFilters } from "./api";
import { SessionFilterBar, readSessionFilters } from "./filters";
import styles from "./conversations.module.css";

export function SessionList() {
  const { t } = useTranslation();
  const { workspace, base: basePath } = useScope();
  const [search, setSearch] = useSearchParams();
  const filters = readSessionFilters(search);
  return (
    <Page
      title={t("Sessions")}
      description={t("Every conversation your workspace has run, and why.")}
      actions={
        workspace.permissions.includes("run") && (
          <Button render={<Link to={`${basePath}/sessions/new`} />}>
            <PlusIcon size={14} aria-hidden="true" />
            {t("New session")}
          </Button>
        )
      }
      toolbar={<SessionFilterBar search={search} setSearch={setSearch} />}
    >
      <SessionResults filters={filters} />
    </Page>
  );
}

function SessionResults({ filters }: { filters: SessionFilters }) {
  const { t } = useTranslation(),
    { workspace, base: basePath } = useScope(),
    navigate = useNavigate(),
    { client } = useAuth(),
    page = useCursor();
  const sessions = useQuery({
    ...conversationQueries(client, workspace.id).sessions(page.cursor, filters),
    placeholderData: keepPreviousData,
    refetchInterval: 5000,
  });
  // A changed query starts a new result set, so its pagination starts over.
  const signature = JSON.stringify(filters);
  const applied = useRef(signature);
  useEffect(() => {
    if (applied.current === signature) return;
    applied.current = signature;
    page.reset();
  }, [signature, page]);
  if (sessions.isPending) return <Loading variant="table" columns={4} />;
  if (!sessions.data)
    return (
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
    );
  const items = sessions.data.items;
  return (
    <>
      <ErrorNotice error={sessions.error} />
      {items.length ? (
        <ResourceTable
          items={items}
          caption={t("Sessions")}
          // The collection opens a session to inspect it: that is the Debug level.
          onRowActivate={(session) =>
            navigate(
              session.selected_thread_id
                ? `${basePath}/sessions/${session.id}/threads/${session.selected_thread_id}?view=debug`
                : `${basePath}/sessions/${session.id}?view=debug`,
            )
          }
          columns={[
            {
              label: t("Session"),
              tone: "primary",
              render: (session) => (
                <ResourceIdentity
                  name={session.preview?.input_text || t("No request text")}
                  resourceId={session.id}
                  icon={
                    <AgentAvatar
                      name={session.preview?.agent_name ?? t("Session")}
                      id={session.id}
                      className={styles.sessionAvatar}
                    />
                  }
                  description={[
                    session.preview?.agent_name,
                    session.run_count === null ||
                    session.run_count === undefined
                      ? undefined
                      : t("{{count}} runs", { count: session.run_count }),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                />
              ),
            },
            {
              label: t("Status"),
              render: (session) =>
                session.preview ? (
                  <StatePill state={session.preview.run_status} />
                ) : (
                  UNKNOWN
                ),
            },
            {
              label: t("Trigger"),
              tone: "muted",
              render: (session) =>
                session.preview
                  ? t(`trigger.${session.preview.trigger}`, {
                      defaultValue: session.preview.trigger,
                    })
                  : UNKNOWN,
            },
            {
              label: t("Updated"),
              tone: "muted",
              align: "right",
              render: (session) => (
                <Timestamp value={session.updated_at} relative />
              ),
            },
          ]}
        />
      ) : (
        <Empty
          title={t("No matching sessions")}
          description={t("Change or clear the search and filters.")}
        />
      )}
      <CollectionFooter
        count={t("{{count}} sessions on this page", { count: items.length })}
      >
        <Pagination page={page} next={sessions.data.next_cursor} />
      </CollectionFooter>
    </>
  );
}
