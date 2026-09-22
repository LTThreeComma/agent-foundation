import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data } from "../../service-client";
import { Page } from "../../shared/page";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { ConnectionEditor } from "./editor";

export function ConnectionsPage() {
  const { client, path, cache, workspace } = useScope();
  const { t } = useTranslation();
  const page = useCursor();
  const [editing, setEditing] = useState<string | null>();
  const query = useQuery({
    queryKey: [...cache, "connections", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}/connections", {
          params: { path, query: { cursor: page.cursor, limit: 50 } },
          signal,
        }),
      ),
  });
  return (
    <Page
      title={t("Connections")}
      description={t(
        "Connect remote tools, then choose which tools each agent can use.",
      )}
      actions={
        workspace.permissions.includes("write") && (
          <Button onClick={() => setEditing(null)}>
            {t("Add connection")}
          </Button>
        )
      }
    >
      <ErrorNotice error={query.error} />
      {!query.data && !query.error ? (
        <Loading />
      ) : (
        <ResourceTable
          items={query.data?.items ?? []}
          onRowActivate={(item) => setEditing(item.id)}
          columns={[
            { label: t("Name"), render: (item) => item.name },
            { label: t("Endpoint"), render: (item) => item.config.url },
            {
              label: t("Authentication"),
              render: (item) =>
                t(
                  item.auth === "none"
                    ? "None"
                    : item.auth === "bearer"
                      ? "Bearer token"
                      : "Headers",
                ),
            },
            {
              label: t("Status"),
              render: (item) => t(item.enabled ? "Enabled" : "Disabled"),
            },
          ]}
        />
      )}
      {query.data?.items.length === 0 && (
        <p>
          {t(
            "No connections yet. Add a remote MCP endpoint to make its tools available.",
          )}
        </p>
      )}
      <Pagination page={page} next={query.data?.next_cursor} />
      {editing !== undefined && (
        <ConnectionEditor
          key={editing ?? "new"}
          id={editing}
          onClose={() => setEditing(undefined)}
          onSaved={async () => {
            setEditing(undefined);
          }}
        />
      )}
    </Page>
  );
}
