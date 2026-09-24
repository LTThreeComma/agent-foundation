import type { Client } from "../../service-client";
import { data, ifMatch, representation, type Schema } from "../../shared/api";
export type WebProviderScope = {
  kind: "workspace" | "organization";
  id: string;
};
/**
 * Providers live in the organization collection: a workspace lists its own and
 * the shared ones and creates its own; the organization creates shared ones.
 */
export function webProviderApi(
  client: Client,
  organizationId: string,
  scope: WebProviderScope,
) {
  const organization_id = organizationId,
    workspace_id = scope.kind === "workspace" ? scope.id : null;
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      client.http
        .GET("/api/v1/organizations/{organization_id}/web-providers", {
          params: {
            path: { organization_id },
            query: { workspace_id, cursor, limit: 100 },
          },
          signal,
        })
        .then(data),
    provider: (provider_id: string, signal: AbortSignal) =>
      client.http
        .GET(
          "/api/v1/organizations/{organization_id}/web-providers/{provider_id}",
          { params: { path: { organization_id, provider_id } }, signal },
        )
        .then(representation),
    createProvider: (body: Omit<Schema["ProviderCreate"], "workspace_id">) =>
      client.http
        .POST("/api/v1/organizations/{organization_id}/web-providers", {
          params: { path: { organization_id } },
          body: { ...body, workspace_id },
        })
        .then(data),
    updateProvider: (
      provider_id: string,
      etag: string,
      body: Schema["ProviderUpdate"],
    ) =>
      client.http
        .PATCH(
          "/api/v1/organizations/{organization_id}/web-providers/{provider_id}",
          {
            params: { path: { organization_id, provider_id } },
            headers: ifMatch(etag),
            body,
          },
        )
        .then(data),
  };
}
