import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useScope } from "../../layout/workspace";
import { data, type components } from "../../service-client";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { saveManagedSelector } from "./managed-flow";
import styles from "./connections.module.css";

type Config = components["schemas"]["OAuthConfig"];
export const emptyOAuth: Config = {
  issuer: "",
  client_id: "",
  scopes: [],
  token_endpoint_auth_method: "none",
};
export function OAuthFields({
  value,
  onChange,
  disabled,
}: {
  value: Config;
  onChange: (value: Config) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [scopes, setScopes] = useState((value.scopes ?? []).join(" "));
  return (
    <>
      <p className={styles.help}>
        {t(
          "Register this Service with your OAuth provider first. Each person connects their own account.",
        )}
      </p>
      <FormField label={t("OAuth issuer")}>
        <Input
          required
          type="url"
          value={value.issuer}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...value, issuer: event.target.value })
          }
        />
      </FormField>
      <FormField label={t("Client ID")}>
        <Input
          required
          value={value.client_id}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...value, client_id: event.target.value })
          }
        />
      </FormField>
      <FormField label={t("Scopes (space separated)")}>
        <Input
          value={scopes}
          disabled={disabled}
          onChange={(event) => {
            setScopes(event.target.value);
            onChange({
              ...value,
              scopes: event.target.value.split(/\s+/).filter(Boolean),
            });
          }}
        />
      </FormField>
      <ChoiceField
        label={t("Client authentication")}
        value={value.token_endpoint_auth_method ?? "none"}
        disabled={disabled}
        options={[
          { value: "none", label: t("Public client (PKCE)") },
          { value: "client_secret_basic", label: t("Client secret (Basic)") },
          { value: "client_secret_post", label: t("Client secret (POST)") },
        ]}
        onValueChange={(method) =>
          onChange({
            ...value,
            token_endpoint_auth_method:
              method as Config["token_endpoint_auth_method"],
          })
        }
      />
    </>
  );
}

export function PersonalAuthorization({
  connectionId,
  draftChanged,
  managed = false,
}: {
  connectionId: string;
  draftChanged: boolean;
  managed?: boolean;
}) {
  const { client, path, cache, base, workspace } = useScope();
  const { t } = useTranslation();
  const queries = useQueryClient();
  const queryKey = [...cache, "connection-authorization", connectionId];
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const query = useQuery({
    queryKey,
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET(
          "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorization",
          {
            params: { path: { ...path, connection_id: connectionId } },
            signal,
          },
        ),
      ),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ||
      (query.state.data?.status === "active" &&
        query.state.data?.operation_kind)
        ? 2000
        : false,
  });
  const active = query.data?.status === "active";
  const expired =
    !!query.data?.expires_at && Date.parse(query.data.expires_at) <= Date.now();
  const label =
    query.data?.status === "reauthorization_required" ||
    (expired && query.data?.status === "pending")
      ? "Reconnect required"
      : active
        ? "Your account is connected"
        : query.data?.status === "pending"
          ? "Authorization pending"
          : "Your account is not connected";
  return (
    <section
      className={styles.form}
      aria-label={t(managed ? "Your connected account" : "Your OAuth account")}
    >
      <strong>
        {t(managed ? "Your connected account" : "Your OAuth account")}
      </strong>
      {query.isPending ? <Loading /> : <p>{t(label)}</p>}
      {query.data?.status === "reauthorization_required" && (
        <p className={styles.help}>
          {t(
            managed
              ? "Account setup is unavailable. Reconnect to start a new enrollment; uncertain requests are not retried automatically."
              : "Authorization is unavailable. Reconnect to continue; an uncertain token request will not be retried automatically.",
          )}
        </p>
      )}
      {managed && query.data?.status === "revoked" && query.data.failure && (
        <p className={styles.help}>
          {t(
            "Local access is disabled. Remote revocation was not confirmed; review the account in Composio Dashboard.",
          )}
        </p>
      )}
      {managed && query.data?.status === "pending" && query.data.failure && (
        <p className={styles.help}>
          {t(
            "The provider result is uncertain. Reconnect to start a new enrollment.",
          )}
        </p>
      )}
      {query.data?.expires_at && (
        <p className={styles.help}>
          {t(
            query.data.status === "pending"
              ? "Authorization expires"
              : "Access token expires",
          )}
          : {new Date(query.data.expires_at).toLocaleString()}
        </p>
      )}
      {draftChanged && (
        <p className={styles.help}>
          {t("Save connection changes before connecting your account.")}
        </p>
      )}
      <div className={styles.row}>
        <Button
          type="button"
          disabled={
            pending ||
            query.isPending ||
            draftChanged ||
            !workspace.permissions.includes("run")
          }
          onClick={async () => {
            setPending(true);
            setError(null);
            try {
              const started = data(
                await client.http.POST(
                  "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorize",
                  {
                    params: { path: { ...path, connection_id: connectionId } },
                    body: {
                      return_url: new URL(
                        `${base}/connections`,
                        window.location.origin,
                      ).href,
                    },
                  },
                ),
              );
              if (managed) {
                if (!started.authorization.id)
                  throw new Error("The authorization selector is missing.");
                saveManagedSelector({
                  workspaceId: path.workspace_id,
                  connectionId,
                  authorizationId: started.authorization.id,
                  generation: started.authorization.generation,
                });
              }
              queries.setQueryData(queryKey, started.authorization);
              window.location.assign(started.redirect_url);
            } catch (failure) {
              setError(failure);
              setPending(false);
            }
          }}
        >
          {t(
            active || query.data?.status === "reauthorization_required"
              ? "Reconnect account"
              : "Connect account",
          )}
        </Button>
        {query.data?.id && query.data.status !== "revoked" && (
          <Button
            type="button"
            variant="outline"
            disabled={pending || !workspace.permissions.includes("run")}
            onClick={async () => {
              setPending(true);
              setError(null);
              try {
                const revoked = data(
                  await client.http.POST(
                    "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/revoke",
                    {
                      params: {
                        path: { ...path, connection_id: connectionId },
                      },
                    },
                  ),
                );
                await queries.cancelQueries({ queryKey });
                queries.setQueryData(queryKey, revoked);
              } catch (failure) {
                setError(failure);
              } finally {
                setPending(false);
              }
            }}
          >
            {t("Disconnect account")}
          </Button>
        )}
      </div>
      <ErrorNotice error={error ?? query.error} />
    </section>
  );
}
