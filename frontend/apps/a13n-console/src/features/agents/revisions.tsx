import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { Section } from "../../shared/page";
import { ResourceTable, Pagination, useCursor } from "../../shared/collection";
import { JsonView } from "../../shared/forms";
import { ErrorNotice } from "../../shared/feedback";

export function RevisionHistory({
  head,
  etag,
  disabled,
  onChanged,
}: {
  head: components["schemas"]["AgentView"];
  etag: string | null;
  disabled: boolean;
  onChanged: () => Promise<void>;
}) {
  const { client, path, cache, workspace } = useScope();
  const { t } = useTranslation();
  const page = useCursor();
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] =
    useState<components["schemas"]["RevisionView"]>();
  const [pending, setPending] = useState(false);
  const query = useQuery({
    queryKey: [...cache, "revisions", head.id, head.version, page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions",
          {
            params: {
              path: { ...path, agent_id: head.id },
              query: { limit: 20, cursor: page.cursor },
            },
            signal,
          },
        ),
      ),
  });
  return (
    <>
      <Section
        title={t("Revision history")}
        description={t(
          "Choose an existing revision as the default without copying it.",
        )}
      >
        <ErrorNotice error={error ?? query.error} />
        <ResourceTable
          items={query.data?.items ?? []}
          onRowActivate={setSelected}
          columns={[
            {
              label: t("Revision"),
              render: (item) => `${t("Revision")} ${item.number}`,
            },
            { label: t("Note"), render: (item) => item.note || "—" },
            {
              label: t("Default"),
              render: (item) =>
                item.id === head.default_revision_id
                  ? t("Current default")
                  : workspace.permissions.includes("write") && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={disabled || pending || !etag}
                        onClick={async () => {
                          if (!etag) return;
                          setPending(true);
                          setError(null);
                          try {
                            await client.http.POST(
                              "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}/set-default",
                              {
                                params: {
                                  path: {
                                    ...path,
                                    agent_id: head.id,
                                    revision_id: item.id,
                                  },
                                },
                                headers: { "If-Match": etag },
                              },
                            );
                            await onChanged();
                          } catch (failure) {
                            setError(failure);
                          } finally {
                            setPending(false);
                          }
                        }}
                      >
                        {t("Set default")}
                      </Button>
                    ),
            },
          ]}
        />
        <Pagination page={page} next={query.data?.next_cursor} />
      </Section>
      <ModalFrame
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(undefined);
        }}
        title={`${t("Revision")} ${selected?.number ?? ""}`}
        closeLabel={t("Close")}
      >
        {selected && <JsonView value={selected.config} />}
      </ModalFrame>
    </>
  );
}
