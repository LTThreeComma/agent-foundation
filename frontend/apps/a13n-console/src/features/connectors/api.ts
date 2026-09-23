import type { Client } from "../../service-client";
import { data, type Schema } from "../../shared/api";
export type ConnectorScope = { kind: "workspace" | "organization"; id: string };
/**
 * Providers live in the organization collection: a workspace lists its own and
 * the shared ones and creates its own; the organization creates shared ones.
 */
export function connectorApi(
  client: Client,
  organizationId: string,
  scope: ConnectorScope,
) {
  const workspace_id = scope.kind === "workspace" ? scope.id : null;
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      client.http
        .GET("/api/v1/organizations/{organization_id}/connector-providers", {
          params: {
            path: { organization_id: organizationId },
            query: { workspace_id, cursor, limit: 100 },
          },
          signal,
        })
        .then(data),
    create: (body: Omit<Schema["ProviderCreate"], "workspace_id">) =>
      client.http
        .POST("/api/v1/organizations/{organization_id}/connector-providers", {
          params: { path: { organization_id: organizationId } },
          body: { ...body, workspace_id },
        })
        .then(data),
  };
}
