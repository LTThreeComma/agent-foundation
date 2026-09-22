import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";
import { Button, FormField, Input, Textarea } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { Page, SaveBar, Section } from "../../shared/page";
import { ResourceTable, Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice, ErrorPage, Loading } from "../../shared/feedback";
import { ModelSelector } from "../models/selector";
import { ConnectionSelector } from "../connections/selector";
import { RevisionHistory } from "./revisions";
import styles from "./agents.module.css";

type Config = components["schemas"]["AgentConfig"];
type Head = components["schemas"]["AgentView"];
export type AgentDraft = {
  head: Head | null;
  etag: string | null;
  config: Config;
};
export function AgentsPage() {
  const { client, path, cache, base, workspace } = useScope();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const page = useCursor();
  const query = useQuery({
    queryKey: [...cache, "agents", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}/agents", {
          params: { path, query: { limit: 50, cursor: page.cursor } },
          signal,
        }),
      ),
  });
  return (
    <Page
      title={t("Agents")}
      description={t(
        "Reusable instructions and immutable configurations for your conversations.",
      )}
      actions={
        workspace.permissions.includes("write") && (
          <Button render={<Link to={`${base}/agents/new`} />}>
            {t("Create agent")}
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
          columns={[
            {
              label: t("Name"),
              render: (item) => (
                <span>
                  {item.name}
                  <small className={styles.secondary}>{item.key}</small>
                </span>
              ),
            },
            {
              label: t("Description"),
              render: (item) => item.description || "—",
            },
          ]}
          onRowActivate={(item) => navigate(`${base}/agents/${item.key}`)}
        />
      )}
      {query.data?.items.length === 0 && (
        <p className={styles.empty}>
          {t("Create your first agent to start a conversation.")}
        </p>
      )}
      <Pagination page={page} next={query.data?.next_cursor} />
    </Page>
  );
}
export function AgentPage() {
  const { agentRef } = useParams();
  const { client, path, cache } = useScope();
  const query = useQuery({
    queryKey: [...cache, "agent", agentRef],
    enabled: !!agentRef,
    queryFn: async ({ signal }): Promise<AgentDraft> => {
      const response = await client.http.GET(
        "/api/v1/workspaces/{workspace_id}/agents/{agent_id}",
        { params: { path: { ...path, agent_id: agentRef ?? "" } }, signal },
      );
      const head = data(response);
      if (!head.default_revision_id)
        throw new Error("Agent has no default revision.");
      const revision = data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}",
          {
            params: {
              path: {
                ...path,
                agent_id: head.id,
                revision_id: head.default_revision_id,
              },
            },
            signal,
          },
        ),
      );
      return {
        head,
        etag: response.response.headers.get("etag"),
        config: revision.config,
      };
    },
  });
  if (agentRef && query.error && !query.data)
    return <ErrorPage error={query.error} />;
  if (agentRef && !query.data) return <Loading />;
  return (
    <AgentEditor
      key={agentRef ?? "new"}
      initial={
        query.data ?? {
          head: null,
          etag: null,
          config: { model_id: "", instructions: "", max_requests: 100 },
        }
      }
      reload={async () => {
        const result = await query.refetch();
        if (!result.data || result.error) throw result.error;
        return result.data;
      }}
    />
  );
}
function AgentEditor({
  initial,
  reload,
}: {
  initial: AgentDraft;
  reload: () => Promise<AgentDraft>;
}) {
  const { client, path, cache, base, workspace } = useScope();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queries = useQueryClient();
  const [baseline, setBaseline] = useState(initial);
  const [config, setConfig] = useState(initial.config);
  const [name, setName] = useState(initial.head?.name ?? "");
  const [key, setKey] = useState(initial.head?.key ?? "");
  const [description, setDescription] = useState(
    initial.head?.description ?? "",
  );
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const editable = workspace.permissions.includes("write");
  const dirty =
    !baseline.head ||
    JSON.stringify(config) !== JSON.stringify(baseline.config);
  const replace = (draft: AgentDraft) => {
    setBaseline(draft);
    setConfig(draft.config);
    setNote("");
    setError(null);
  };
  async function refresh() {
    try {
      replace(await reload());
    } catch (failure) {
      setError(failure);
    }
  }
  return (
    <Page
      title={baseline.head?.name ?? t("Create agent")}
      back={`${base}/agents`}
      description={
        baseline.head
          ? t("Save instructions as a new immutable revision.")
          : t("Choose a model and give your agent its instructions.")
      }
    >
      <form
        className={styles.editor}
        onSubmit={async (event) => {
          event.preventDefault();
          if (!editable || pending || !config.model_id) return;
          setPending(true);
          setError(null);
          try {
            if (baseline.head) {
              if (!baseline.etag)
                throw new Error(
                  "The agent version is unavailable. Reload before saving.",
                );
              await client.http.POST(
                "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions",
                {
                  params: { path: { ...path, agent_id: baseline.head.id } },
                  headers: { "If-Match": baseline.etag },
                  body: { config, note: note || null, make_default: true },
                },
              );
              await refresh();
            } else {
              const head = data(
                await client.http.POST(
                  "/api/v1/workspaces/{workspace_id}/agents",
                  {
                    params: { path },
                    body: { name, key, description, config },
                  },
                ),
              );
              navigate(`${base}/agents/${head.key}`, { replace: true });
            }
            await queries.invalidateQueries({ queryKey: [...cache, "agents"] });
          } catch (failure) {
            setError(failure);
          } finally {
            setPending(false);
          }
        }}
      >
        {!baseline.head && (
          <Section title={t("Identity")}>
            <div className={styles.fields}>
              <FormField label={t("Name")}>
                <Input
                  required
                  maxLength={128}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </FormField>
              <FormField
                label={t("URL key")}
                description={t(
                  "Lowercase letters, numbers, hyphens and underscores.",
                )}
              >
                <Input
                  required
                  pattern="[a-z0-9][a-z0-9_-]{0,127}"
                  value={key}
                  onChange={(event) => setKey(event.target.value)}
                />
              </FormField>
              <FormField label={t("Description")}>
                <Input
                  maxLength={8192}
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </FormField>
            </div>
          </Section>
        )}
        <Section title={t("Configuration")}>
          <div className={styles.fields}>
            <ModelSelector
              value={config.model_id}
              onChange={(model_id) => setConfig({ ...config, model_id })}
              disabled={!editable || pending}
            />
            <FormField label={t("Instructions")}>
              <Textarea
                rows={12}
                maxLength={65536}
                value={config.instructions ?? ""}
                disabled={!editable || pending}
                onChange={(event) =>
                  setConfig({ ...config, instructions: event.target.value })
                }
              />
            </FormField>
            <div className={styles.limits}>
              <FormField label={t("Maximum requests")}>
                <Input
                  type="number"
                  required
                  min={1}
                  max={1000}
                  value={config.max_requests ?? 100}
                  disabled={!editable || pending}
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      max_requests: Number(event.target.value),
                    })
                  }
                />
              </FormField>
              <FormField
                label={t("Compaction threshold")}
                description={t("Optional token threshold.")}
              >
                <Input
                  type="number"
                  min={1}
                  max={10000000}
                  value={config.compaction_trigger_tokens ?? ""}
                  disabled={!editable || pending}
                  onChange={(event) =>
                    setConfig({
                      ...config,
                      compaction_trigger_tokens: event.target.value
                        ? Number(event.target.value)
                        : null,
                    })
                  }
                />
              </FormField>
            </div>
          </div>
        </Section>
        <Section title={t("Tools")}>
          <ConnectionSelector
            value={config.connections ?? []}
            onChange={(connections) => setConfig({ ...config, connections })}
            disabled={!editable || pending}
          />
        </Section>
        <ErrorNotice error={error} />
        {!!error && baseline.head && (
          <Button type="button" variant="outline" onClick={refresh}>
            {t("Discard edits and reload")}
          </Button>
        )}
        {editable && dirty && (
          <SaveBar
            title={t("Unsaved changes")}
            consequence={t(
              "Saving creates a revision and makes it the default.",
            )}
            saveLabel={t("Save agent")}
            pending={pending}
            disabled={!config.model_id}
            note={note}
            onNoteChange={setNote}
            onDiscard={
              baseline.head
                ? () => replace(baseline)
                : () => navigate(`${base}/agents`)
            }
          />
        )}
      </form>
      {baseline.head && (
        <RevisionHistory
          head={baseline.head}
          etag={baseline.etag}
          disabled={dirty || pending}
          onChanged={refresh}
        />
      )}
    </Page>
  );
}
