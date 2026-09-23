import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { ErrorNotice, Loading } from "../../shared/feedback";
import {
  FormActions,
  HeaderFields,
  serializeHeaders,
  type HeaderDraft,
} from "../../shared/forms";
import { ManagedForm } from "./managed-editor";
import { ToolSelection } from "./tool-selection";
import { emptyOAuth, OAuthFields, PersonalAuthorization } from "./oauth";
import styles from "./connections.module.css";

type Connection = components["schemas"]["ConnectionView"];
type Tool = components["schemas"]["ToolInfo"];
type Auth = Connection["auth"];
export function ConnectionEditor({
  id,
  onClose,
  onSaved,
}: {
  id: string | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { client, path, cache } = useScope();
  const { t } = useTranslation();
  const [kind, setKind] = useState("mcp");
  const query = useQuery({
    queryKey: [...cache, "connection", id],
    enabled: !!id,
    refetchOnMount: "always",
    queryFn: async ({ signal }) => {
      const response = await client.http.GET(
        "/api/v1/workspaces/{workspace_id}/connections/{connection_id}",
        { params: { path: { ...path, connection_id: id! } }, signal },
      );
      return {
        value: data(response),
        etag: response.response.headers.get("etag"),
      };
    },
  });
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t(id ? "Edit connection" : "Add connection")}
      closeLabel={t("Close")}
    >
      {id && (!query.data || !query.isFetchedAfterMount) ? (
        <>
          <ErrorNotice error={query.error} />
          {!query.error && <Loading />}
        </>
      ) : (
        <>
          {!id && (
            <ChoiceField
              label={t("Connection type")}
              value={kind}
              options={[
                { value: "mcp", label: t("Remote MCP") },
                { value: "composio", label: "Composio" },
              ]}
              onValueChange={setKind}
            />
          )}
          {(query.data?.value.type ?? kind) === "composio" ? (
            <ManagedForm
              loaded={{
                initial: query.data?.value,
                etag: query.data?.etag ?? null,
              }}
              onClose={onClose}
              onSaved={onSaved}
            />
          ) : (
            <ConnectionForm
              loaded={{
                initial: query.data?.value,
                etag: query.data?.etag ?? null,
              }}
              onClose={onClose}
              onSaved={onSaved}
            />
          )}
        </>
      )}
    </ModalFrame>
  );
}
function ConnectionForm({
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
  const [{ initial, etag }] = useState(loaded);
  const initialConfig =
    initial && "url" in initial.config ? initial.config : undefined;
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name ?? "");
  const [url, setUrl] = useState(initialConfig?.url ?? "");
  const [auth, setAuth] = useState<Auth>(initial?.auth ?? "none");
  const [replace, setReplace] = useState(!initial?.credential_configured);
  const [oauth, setOAuth] = useState(initialConfig?.oauth ?? emptyOAuth);
  const [token, setToken] = useState("");
  const [headers, setHeaders] = useState<HeaderDraft[]>([]);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [tools, setTools] = useState<string[] | null>(
    initialConfig?.tools ?? null,
  );
  const [safe, setSafe] = useState<string[]>(
    initialConfig?.recovery_retry_safe_tools ?? [],
  );
  const [discovered, setDiscovered] = useState<Tool[]>();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const editable = workspace.permissions.includes("write");
  const identityChanged =
    !!initial &&
    (url !== initialConfig?.url ||
      auth !== initial.auth ||
      (auth === "oauth" &&
        JSON.stringify(oauth) !== JSON.stringify(initialConfig?.oauth)) ||
      (replace &&
        auth !== "none" &&
        !(auth === "oauth" && oauth.token_endpoint_auth_method === "none")));
  const [reaffirm, setReaffirm] = useState(false);
  const canTest =
    !!initial &&
    !identityChanged &&
    !pending &&
    url === initialConfig?.url &&
    auth === initial.auth &&
    !token &&
    headers.length === 0;
  return (
    <form
      className={styles.form}
      onSubmit={async (event) => {
        event.preventDefault();
        if (!editable || pending) return;
        setError(null);
        setPending(true);
        try {
          let credential:
            components["schemas"]["ConnectionCreate"]["credential"] | undefined;
          if (
            auth === "none" ||
            (auth === "oauth" && oauth.token_endpoint_auth_method === "none")
          )
            credential = null;
          else if (replace || auth !== initial?.auth) {
            if (auth === "bearer") credential = { token };
            else if (auth === "oauth") credential = { client_secret: token };
            else {
              const values = serializeHeaders(headers, []);
              credential = {
                headers: Object.fromEntries(
                  Object.entries(values).filter(
                    (entry): entry is [string, string] => entry[1] !== null,
                  ),
                ),
              };
            }
          }
          const config = {
            url,
            oauth: auth === "oauth" ? oauth : null,
            tools,
            recovery_retry_safe_tools: identityChanged && !reaffirm ? [] : safe,
          };
          let response;
          if (initial) {
            if (!etag)
              throw new Error(
                "The connection version is unavailable. Close and reopen before saving.",
              );
            response = await client.http.PATCH(
              "/api/v1/workspaces/{workspace_id}/connections/{connection_id}",
              {
                params: { path: { ...path, connection_id: initial.id } },
                headers: { "If-Match": etag },
                body: {
                  name,
                  config,
                  auth,
                  enabled,
                  ...(credential !== undefined ? { credential } : {}),
                },
              },
            );
          } else
            response = await client.http.POST(
              "/api/v1/workspaces/{workspace_id}/connections",
              {
                params: { path },
                body: { type: "mcp", name, config, auth, credential },
              },
            );
          const saved = data(response);
          const queryKey = [...cache, "connection", saved.id];
          await queries.cancelQueries({ queryKey });
          queries.setQueryData(queryKey, {
            value: saved,
            etag: response.response.headers.get("etag"),
          });
          setToken("");
          setHeaders([]);
          await queries.invalidateQueries({
            queryKey: [...cache, "connections"],
          });
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
          "Remote MCP tools. Credentials are encrypted and never shown after saving.",
        )}
      </p>
      <FormField label={t("Name")}>
        <Input
          required
          maxLength={128}
          value={name}
          disabled={!editable || pending}
          onChange={(event) => setName(event.target.value)}
        />
      </FormField>
      <FormField label={t("Endpoint URL")}>
        <Input
          required
          type="url"
          value={url}
          disabled={!editable || pending}
          onChange={(event) => {
            setUrl(event.target.value);
            setDiscovered(undefined);
            setReaffirm(false);
          }}
          placeholder="https://tools.example.com/mcp"
        />
      </FormField>
      <ChoiceField
        label={t("Authentication")}
        value={auth}
        disabled={!editable || pending}
        options={[
          { value: "none", label: t("None") },
          { value: "bearer", label: t("Bearer token") },
          { value: "headers", label: t("Headers") },
          { value: "oauth", label: t("OAuth (personal account)") },
        ]}
        onValueChange={(value) => {
          setAuth(value as Auth);
          setReplace(true);
          setToken("");
          setHeaders([]);
          setReaffirm(false);
        }}
      />
      {auth === "oauth" && (
        <OAuthFields
          value={oauth}
          disabled={!editable || pending}
          onChange={(value) => {
            setOAuth(value);
            setReaffirm(false);
            if (
              value.token_endpoint_auth_method !==
              oauth.token_endpoint_auth_method
            ) {
              setReplace(true);
              setToken("");
            }
          }}
        />
      )}
      {auth !== "none" &&
        !(auth === "oauth" && oauth.token_endpoint_auth_method === "none") &&
        !replace && (
          <div className={styles.row}>
            <span>{t("Credential saved")}</span>
            {editable && (
              <Button
                type="button"
                variant="outline"
                onClick={() => setReplace(true)}
              >
                {t("Replace credential")}
              </Button>
            )}
          </div>
        )}
      {auth !== "none" &&
        !(auth === "oauth" && oauth.token_endpoint_auth_method === "none") &&
        replace && (
          <>
            {auth === "bearer" || auth === "oauth" ? (
              <FormField
                label={t(auth === "oauth" ? "Client secret" : "Bearer token")}
              >
                <Input
                  type="password"
                  required
                  value={token}
                  autoComplete="new-password"
                  disabled={!editable || pending}
                  onChange={(event) => setToken(event.target.value)}
                />
              </FormField>
            ) : (
              <HeaderFields
                rows={headers}
                onChange={setHeaders}
                disabled={!editable || pending}
              />
            )}
            {initial?.credential_configured && auth === initial.auth && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setReplace(false);
                  setToken("");
                  setHeaders([]);
                }}
              >
                {t("Keep saved credential")}
              </Button>
            )}
          </>
        )}
      {initial && (
        <label className={styles.row}>
          <span>{t("Enabled")}</span>
          <input
            type="checkbox"
            checked={enabled}
            disabled={!editable || pending}
            onChange={(event) => setEnabled(event.target.checked)}
          />
        </label>
      )}
      {initial && (
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={!canTest || !editable}
            onClick={async () => {
              setPending(true);
              setError(null);
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
                setDiscovered(result.tools);
                if (tools === null)
                  setTools(result.tools.map((tool) => tool.name));
              } catch (failure) {
                setError(failure);
              } finally {
                if (initial.auth === "oauth")
                  await queries.invalidateQueries({
                    queryKey: [
                      ...cache,
                      "connection-authorization",
                      initial.id,
                    ],
                  });
                setPending(false);
              }
            }}
          >
            {t(pending ? "Working…" : "Test connection")}
          </Button>
          {!canTest && !pending && (
            <p className={styles.help}>
              {t("Save endpoint or credential changes before testing.")}
            </p>
          )}
        </div>
      )}
      {(discovered || tools?.length) && (
        <ToolSelection
          tools={tools}
          safe={safe}
          discovered={discovered}
          setTools={setTools}
          setSafe={setSafe}
          disabled={!editable || pending}
        />
      )}
      {identityChanged && safe.length > 0 && (
        <label className={styles.help}>
          <input
            type="checkbox"
            checked={reaffirm}
            onChange={(event) => setReaffirm(event.target.checked)}
          />
          {t(
            "Reconfirm retry safety for this endpoint and credential. Otherwise saving clears the declarations.",
          )}
        </label>
      )}
      <ErrorNotice error={error} />
      <FormActions
        onCancel={onClose}
        pending={pending}
        label={t("Save connection")}
        disabled={
          !editable || (auth === "headers" && replace && headers.length === 0)
        }
      />
      {initial?.auth === "oauth" && (
        <PersonalAuthorization
          connectionId={initial.id}
          draftChanged={
            pending ||
            identityChanged ||
            name !== initial.name ||
            enabled !== initial.enabled ||
            JSON.stringify(tools) !==
              JSON.stringify(initialConfig?.tools ?? null) ||
            JSON.stringify(safe) !==
              JSON.stringify(initialConfig?.recovery_retry_safe_tools ?? [])
          }
        />
      )}
    </form>
  );
}
