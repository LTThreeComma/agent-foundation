import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  SchemaFields,
  withSchemaValues,
  jsonObject,
  validateSettings,
} from "../../shared/forms";
import { managedSetupSchema } from "./managed-schema";
import { PersonalAuthorization } from "./oauth";
import { ToolSelection } from "./tool-selection";
import styles from "./connections.module.css";

type Connection = components["schemas"]["ConnectionView"];
type Catalog = components["schemas"]["CatalogView"];
type Config = components["schemas"]["ComposioConfig"];
export function ManagedForm({
  loaded,
  onClose,
  onSaved,
}: {
  loaded: { initial?: Connection; etag: string | null };
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { client, path, cache, workspace } = useScope();
  const queries = useQueryClient();
  const { t } = useTranslation();
  const [{ initial, etag }] = useState(loaded);
  const original =
    initial && "app" in initial.config ? initial.config : undefined;
  const [name, setName] = useState(initial?.name ?? "");
  const [apiKey, setApiKey] = useState("");
  const [replace, setReplace] = useState(!initial);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [app, setApp] = useState(original?.app ?? "");
  const [setup, setSetup] = useState<Record<string, unknown>>(
    original
      ? {
          auth_config_id: original.auth_config_id,
          toolkit_version: original.toolkit_version,
          connection_data: original.connection_data ?? {},
        }
      : {},
  );
  const [selected, setSelected] = useState<string[]>(original?.actions ?? []);
  const [catalog, setCatalog] = useState<Catalog>();
  const [apps, setApps] = useState<Catalog["apps"]>([]);
  const [tested, setTested] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const editable = workspace.permissions.includes("write");
  const application = catalog?.apps.find((item) => item.key === app);
  const schema = application
    ? managedSetupSchema(application.setup_schema, setup.auth_config_id)
    : undefined;
  const config = { app, ...setup, actions: selected } as Config;
  const changed =
    replace ||
    !original ||
    app !== original.app ||
    setup.auth_config_id !== original.auth_config_id ||
    setup.toolkit_version !== original.toolkit_version ||
    JSON.stringify(selected) !== JSON.stringify(original.actions) ||
    JSON.stringify(setup.connection_data ?? {}) !==
      JSON.stringify(original.connection_data ?? {});
  async function discover(application?: string) {
    setPending(true);
    setError(null);
    try {
      const result = data(
        await client.http.POST(
          "/api/v1/workspaces/{workspace_id}/connection-catalog/composio",
          {
            params: { path },
            body: {
              ...(replace
                ? { credential: { api_key: apiKey } }
                : { connection_id: initial!.id }),
              app: application ?? null,
              toolkit_version:
                original && application === original.app
                  ? original.toolkit_version
                  : null,
            },
          },
        ),
      );
      if (application) setCatalog(result);
      else setApps(result.apps);
      if (application) {
        setApp(application);
        setSetup(
          withSchemaValues(
            result.apps[0]!.setup_schema,
            application === original?.app ? setup : {},
          ),
        );
        if (application !== app) setSelected([]);
        if (!name) setName(result.apps[0]!.name);
      }
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
    }
  }
  return (
    <form
      className={styles.form}
      onSubmit={async (event) => {
        event.preventDefault();
        if (!editable || pending) return;
        setPending(true);
        setError(null);
        try {
          if (schema) validateSettings(schema, setup);
          const safeConfig = {
            ...config,
            connection_data: jsonObject(
              JSON.stringify(config.connection_data ?? {}),
            ),
          };
          const credential = replace ? { api_key: apiKey } : undefined;
          if (initial) {
            if (!etag)
              throw new Error(
                "Close and reopen this connection before saving.",
              );
            data(
              await client.http.PATCH(
                "/api/v1/workspaces/{workspace_id}/connections/{connection_id}",
                {
                  params: { path: { ...path, connection_id: initial.id } },
                  headers: { "If-Match": etag },
                  body: {
                    name,
                    config: safeConfig,
                    auth: "managed",
                    enabled,
                    ...(credential ? { credential } : {}),
                  },
                },
              ),
            );
          } else
            data(
              await client.http.POST(
                "/api/v1/workspaces/{workspace_id}/connections",
                {
                  params: { path },
                  body: {
                    name,
                    type: "composio",
                    auth: "managed",
                    config: safeConfig,
                    credential,
                  },
                },
              ),
            );
          setApiKey("");
          await queries.invalidateQueries({ queryKey: cache });
          await onSaved();
        } catch (failure) {
          setError(failure);
        } finally {
          setPending(false);
        }
      }}
    >
      <p className={styles.help}>
        {t(
          "Choose an application and actions. Each person connects their own account after you save.",
        )}
      </p>
      <FormField label={t("Project API key")}>
        {replace ? (
          <Input
            type="password"
            autoComplete="off"
            required
            value={apiKey}
            disabled={!editable || pending}
            onChange={(event) => setApiKey(event.target.value)}
          />
        ) : (
          <div className={styles.row}>
            <span>{t("Saved")}</span>
            <Button
              type="button"
              variant="outline"
              disabled={!editable}
              onClick={() => setReplace(true)}
            >
              {t("Replace")}
            </Button>
          </div>
        )}
      </FormField>
      {replace && (
        <>
          <a
            href="https://dashboard.composio.dev"
            target="_blank"
            rel="noreferrer"
          >
            {t("Open Composio Dashboard")} ↗
          </a>
        </>
      )}
      <Button
        type="button"
        variant="outline"
        disabled={(replace && !apiKey) || pending || !editable}
        onClick={() => void discover()}
      >
        {t("Load applications")}
      </Button>
      {apps.length > 0 && (
        <ChoiceField
          label={t("Application")}
          value={app}
          options={apps.map((item) => ({ value: item.key, label: item.name }))}
          onValueChange={(value) => void discover(value)}
          disabled={pending}
        />
      )}
      {application?.unavailable_reason && (
        <p className={styles.help}>{application.unavailable_reason}</p>
      )}
      {schema && !application?.unavailable_reason && (
        <SchemaFields
          schema={schema}
          value={setup}
          onChange={(value) =>
            setSetup(
              withSchemaValues(
                managedSetupSchema(
                  application!.setup_schema,
                  value.auth_config_id,
                ),
                value.auth_config_id === setup.auth_config_id
                  ? value
                  : { ...value, connection_data: {} },
              ),
            )
          }
        />
      )}
      {!schema && original && (
        <p>
          {original.app} · {original.toolkit_version}
        </p>
      )}
      <FormField label={t("Name")}>
        <Input
          required
          value={name}
          maxLength={128}
          disabled={!editable || pending}
          onChange={(event) => setName(event.target.value)}
        />
      </FormField>
      <ToolSelection
        tools={selected}
        safe={[]}
        setSafe={() => {}}
        setTools={setSelected}
        discovered={catalog?.tools.length ? catalog.tools : undefined}
        disabled={!editable || pending}
        allowRecovery={false}
      />
      {initial && (
        <>
          <label>
            <input
              type="checkbox"
              checked={enabled}
              disabled={!editable || pending}
              onChange={(event) => setEnabled(event.target.checked)}
            />{" "}
            {t("Enabled")}
          </label>
          <Button
            type="button"
            variant="outline"
            disabled={
              pending ||
              changed ||
              !editable ||
              !workspace.permissions.includes("run")
            }
            onClick={async () => {
              setPending(true);
              setError(null);
              setTested(false);
              try {
                const result = data(
                  await client.http.POST(
                    "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/test",
                    {
                      params: { path: { ...path, connection_id: initial.id } },
                    },
                  ),
                );
                if (result.version !== initial.version)
                  throw new Error(
                    "The connection changed. Close and reopen before testing.",
                  );
                setTested(true);
              } catch (failure) {
                setError(failure);
              } finally {
                setPending(false);
              }
            }}
          >
            {t("Test connection")}
          </Button>
          {tested && (
            <p>{t("Your account and selected actions are available.")}</p>
          )}
          <PersonalAuthorization
            connectionId={initial.id}
            draftChanged={pending || changed}
            managed
          />
        </>
      )}
      <ErrorNotice error={error} />
      <FormActions
        pending={pending}
        onCancel={onClose}
        disabled={
          !editable || !app || selected.length === 0 || (replace && !apiKey)
        }
      />
    </form>
  );
}
