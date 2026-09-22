import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { Page, Section } from "../../shared/page";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { ErrorNotice } from "../../shared/feedback";
import { SchemaFields, withSchemaValues, jsonObject } from "../../shared/forms";
import { credentialMode } from "../../shared/provider-authentication";
import styles from "./models.module.css";

type Provider = components["schemas"]["ProviderView"];
type ModelConfig = components["schemas"]["ModelConfig"];
function useProviders() {
  const { client, cache, workspace } = useScope();
  const page = useCursor();
  const query = useQuery({
    queryKey: [...cache, "providers", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/organizations/{organization_id}/model-providers",
          {
            params: {
              path: { organization_id: workspace.organization_id },
              query: {
                workspace_id: workspace.id,
                limit: 50,
                cursor: page.cursor,
              },
            },
            signal,
          },
        ),
      ),
  });
  return { query, page };
}
export function ModelsPage() {
  const { client, cache, workspace } = useScope();
  const { t } = useTranslation();
  const queries = useQueryClient();
  const page = useCursor();
  const providers = useProviders();
  const [dialog, setDialog] = useState<"model" | "provider" | null>(null);
  const query = useQuery({
    queryKey: [...cache, "models", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/organizations/{organization_id}/models",
          {
            params: {
              path: { organization_id: workspace.organization_id },
              query: {
                workspace_id: workspace.id,
                limit: 50,
                cursor: page.cursor,
              },
            },
            signal,
          },
        ),
      ),
  });
  const close = () => setDialog(null);
  const saved = async () => {
    close();
    await queries.invalidateQueries({ queryKey: cache });
  };
  return (
    <Page
      title={t("Models")}
      description={t(
        "Connect a provider, then add the models your agents can use.",
      )}
      actions={
        workspace.permissions.includes("write") && (
          <Button onClick={() => setDialog("model")}>{t("Add model")}</Button>
        )
      }
    >
      <ErrorNotice error={query.error} />
      <ResourceTable
        items={query.data?.items ?? []}
        columns={[
          { label: t("Name"), render: (item) => item.name },
          { label: t("Model ID"), render: (item) => item.config.model_name },
          { label: t("API"), render: (item) => item.config.model_api },
          {
            label: t("Status"),
            render: (item) => t(item.enabled ? "Enabled" : "Disabled"),
          },
        ]}
      />
      {query.data?.items.length === 0 && (
        <p className={styles.empty}>
          {t("No models yet. Add a model to configure an agent.")}
        </p>
      )}
      <Pagination page={page} next={query.data?.next_cursor} />
      <Section
        title={t("Model providers")}
        description={t(
          "Credentials are stored securely and are never returned.",
        )}
        actions={
          workspace.permissions.includes("write") && (
            <Button variant="outline" onClick={() => setDialog("provider")}>
              {t("Add provider")}
            </Button>
          )
        }
      >
        <ErrorNotice error={providers.query.error} />
        <ResourceTable
          items={providers.query.data?.items ?? []}
          columns={[
            { label: t("Name"), render: (item) => item.name },
            { label: t("Type"), render: (item) => item.type },
            {
              label: t("Credential"),
              render: (item) =>
                t(item.credential_configured ? "Saved" : "Not configured"),
            },
          ]}
        />
        <Pagination
          page={providers.page}
          next={providers.query.data?.next_cursor}
        />
      </Section>
      {dialog === "provider" && (
        <ProviderForm onClose={close} onSaved={saved} />
      )}
      {dialog === "model" && (
        <ModelForm
          onClose={close}
          onSaved={saved}
          onCreateProvider={() => setDialog("provider")}
        />
      )}
    </Page>
  );
}
function ProviderForm({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { client, cache, workspace } = useScope();
  const { t } = useTranslation();
  const page = useCursor();
  const catalog = useQuery({
    queryKey: [...cache, "provider-types", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/provider-types/model", {
          params: { query: { limit: 50, cursor: page.cursor } },
          signal,
        }),
      ),
  });
  const [type, setType] = useState("");
  const [name, setName] = useState("");
  const [configuration, setConfiguration] = useState<Record<string, unknown>>(
    {},
  );
  const [credential, setCredential] = useState<Record<string, unknown>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const definition = catalog.data?.items.find((item) => item.type === type);
  const mode = credentialMode(definition, configuration);
  const close = () => {
    setCredential({});
    onClose();
  };
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={t("Add provider")}
      closeLabel={t("Close")}
      description={t("Saving a provider does not test its connection.")}
    >
      <form
        className={styles.form}
        onSubmit={async (event) => {
          event.preventDefault();
          if (!definition) return;
          setPending(true);
          setError(null);
          try {
            await client.http.POST(
              "/api/v1/organizations/{organization_id}/model-providers",
              {
                params: {
                  path: { organization_id: workspace.organization_id },
                },
                body: {
                  workspace_id: workspace.id,
                  type,
                  name,
                  config: jsonObject(
                    JSON.stringify(
                      withSchemaValues(
                        definition.configuration_schema,
                        configuration,
                      ),
                    ),
                  ),
                  credential:
                    mode === "forbidden" ||
                    (mode === "optional" &&
                      Object.keys(credential).length === 0)
                      ? null
                      : jsonObject(
                          JSON.stringify(
                            withSchemaValues(
                              definition.credential_schema ?? {},
                              credential,
                            ),
                          ),
                        ),
                },
              },
            );
            setCredential({});
            await onSaved();
          } catch (failure) {
            setError(failure);
          } finally {
            setPending(false);
          }
        }}
      >
        <ChoiceField
          label={t("Provider")}
          value={type}
          required
          options={
            catalog.data?.items.map((item) => ({
              value: item.type,
              label: item.display_name,
            })) ?? []
          }
          onValueChange={(value) => {
            setType(value);
            setConfiguration({});
            setCredential({});
          }}
        />
        <Pagination page={page} next={catalog.data?.next_cursor} />
        {definition && (
          <>
            {mode !== "forbidden" && (
              <SchemaFields
                schema={definition.credential_schema ?? {}}
                value={credential}
                onChange={setCredential}
                secret
                requireFields={mode === "required"}
              />
            )}
            {definition.setup_url && (
              <a href={definition.setup_url} target="_blank" rel="noreferrer">
                {t("Get credentials")} ↗
              </a>
            )}
            <SchemaFields
              schema={definition.configuration_schema}
              value={configuration}
              onChange={(value) => {
                setConfiguration(value);
                if (credentialMode(definition, value) === "forbidden")
                  setCredential({});
              }}
            />
            <FormField label={t("Name")}>
              <Input
                required
                maxLength={128}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
          </>
        )}
        <ErrorNotice error={error ?? catalog.error} />
        <div className={styles.actions}>
          <Button type="button" variant="ghost" onClick={close}>
            {t("Cancel")}
          </Button>
          <Button
            type="submit"
            disabled={!definition || pending}
            loading={pending}
          >
            {t("Save provider")}
          </Button>
        </div>
      </form>
    </ModalFrame>
  );
}
function ModelForm({
  onClose,
  onSaved,
  onCreateProvider,
}: {
  onClose: () => void;
  onSaved: () => Promise<void>;
  onCreateProvider: () => void;
}) {
  const { client, workspace } = useScope();
  const { t } = useTranslation();
  const providers = useProviders();
  const [provider, setProvider] = useState<Provider | null>(null);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [config, setConfig] = useState<ModelConfig>({
    model_name: "",
    model_api: "openai.responses",
    context_window: 32768,
  });
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("Add model")}
      closeLabel={t("Close")}
      description={t(
        "Use the exact upstream model ID supplied by your provider.",
      )}
    >
      <form
        className={styles.form}
        onSubmit={async (event) => {
          event.preventDefault();
          if (!provider) return;
          setPending(true);
          setError(null);
          try {
            await client.http.POST(
              "/api/v1/organizations/{organization_id}/models",
              {
                params: {
                  path: { organization_id: workspace.organization_id },
                },
                body: {
                  workspace_id: workspace.id,
                  provider_id: provider.id,
                  name,
                  key,
                  config,
                },
              },
            );
            await onSaved();
          } catch (failure) {
            setError(failure);
          } finally {
            setPending(false);
          }
        }}
      >
        <ChoiceField
          label={t("Provider")}
          value={provider?.id}
          required
          options={
            [
              ...(provider &&
              !providers.query.data?.items.some(
                (item) => item.id === provider.id,
              )
                ? [provider]
                : []),
              ...(providers.query.data?.items ?? []),
            ].map((item) => ({
              value: item.id,
              label: item.name,
              disabled: !item.enabled,
            })) ?? []
          }
          onValueChange={(id) =>
            setProvider(
              providers.query.data?.items.find((item) => item.id === id) ??
                null,
            )
          }
        />
        <Pagination
          page={providers.page}
          next={providers.query.data?.next_cursor}
        />
        <Button type="button" variant="outline" onClick={onCreateProvider}>
          {t("Add provider")}
        </Button>
        <FormField label={t("Name")}>
          <Input
            required
            maxLength={128}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        <FormField label={t("Key")}>
          <Input
            required
            pattern="[a-z0-9][a-z0-9_-]{0,127}"
            value={key}
            onChange={(event) => setKey(event.target.value)}
          />
        </FormField>
        <FormField label={t("Model ID")}>
          <Input
            required
            maxLength={256}
            value={config.model_name}
            onChange={(event) =>
              setConfig({ ...config, model_name: event.target.value })
            }
          />
        </FormField>
        <ChoiceField
          label={t("API")}
          value={config.model_api}
          options={[
            { value: "openai.responses", label: "Responses" },
            { value: "openai.chat_completions", label: "Chat Completions" },
          ]}
          onValueChange={(value) => {
            if (
              value === "openai.responses" ||
              value === "openai.chat_completions"
            )
              setConfig({ ...config, model_api: value });
          }}
        />
        <FormField label={t("Context window")}>
          <Input
            type="number"
            min={1}
            max={10000000}
            required
            value={config.context_window}
            onChange={(event) =>
              setConfig({
                ...config,
                context_window: Number(event.target.value),
              })
            }
          />
        </FormField>
        <FormField label={t("Maximum output tokens")}>
          <Input
            type="number"
            min={1}
            max={1000000}
            value={config.max_tokens ?? ""}
            onChange={(event) =>
              setConfig({
                ...config,
                max_tokens: event.target.value
                  ? Number(event.target.value)
                  : null,
              })
            }
          />
        </FormField>
        <ErrorNotice error={error ?? providers.query.error} />
        <div className={styles.actions}>
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("Cancel")}
          </Button>
          <Button
            type="submit"
            disabled={!provider || pending}
            loading={pending}
          >
            {t("Save model")}
          </Button>
        </div>
      </form>
    </ModalFrame>
  );
}
