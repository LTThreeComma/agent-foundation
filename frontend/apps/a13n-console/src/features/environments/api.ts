import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import { data, representation, type Schema } from "../../shared/api";
export type EnvironmentScope = {
  kind: "workspace" | "organization";
  id: string;
};
/** Templates belong to one workspace. */
export type WorkspaceScope = EnvironmentScope & { kind: "workspace" };
/**
 * Providers live in the organization collection: a workspace lists its own and
 * the shared ones and creates its own; the organization creates shared ones.
 */
export function environmentApi(
  client: Client,
  organizationId: string,
  scope: EnvironmentScope,
) {
  const workspace_id = scope.kind === "workspace" ? scope.id : null;
  return {
    providers: (signal: AbortSignal, cursor?: string) =>
      client.http
        .GET("/api/v1/organizations/{organization_id}/environment-providers", {
          params: {
            path: { organization_id: organizationId },
            query: { workspace_id, cursor, limit: 100 },
          },
          signal,
        })
        .then(data),
    createProvider: (body: Omit<Schema["ProviderCreate"], "workspace_id">) =>
      client.http
        .POST("/api/v1/organizations/{organization_id}/environment-providers", {
          params: { path: { organization_id: organizationId } },
          body: { ...body, workspace_id },
        })
        .then(data),
    /** A read-only probe of the saved account: it creates or starts nothing. */
    testProvider: (provider_id: string) =>
      client.http
        .POST(
          "/api/v1/organizations/{organization_id}/environment-providers/{provider_id}/test",
          {
            params: { path: { organization_id: organizationId, provider_id } },
          },
        )
        .then(data),
  };
}

export function environmentTemplates(
  client: Client,
  workspaceId: string,
  signal: AbortSignal,
  cursor?: string,
) {
  return client.http
    .GET("/api/v1/workspaces/{workspace_id}/environment-templates", {
      params: { path: { workspace_id: workspaceId }, query: { cursor } },
      signal,
    })
    .then(data);
}

/**
 * Reserve a managed environment from a template. It starts `creating`; the
 * Service's maintenance creates the instance.
 */
export function createManagedEnvironment(
  client: Client,
  workspaceId: string,
  body: Schema["ManagedEnvironmentCreate"],
) {
  return client.http
    .POST("/api/v1/workspaces/{workspace_id}/environments", {
      params: { path: { workspace_id: workspaceId } },
      body,
    })
    .then(data);
}

export function environmentQuery(
  client: Client,
  workspaceId: string,
  id: string,
) {
  return queryOptions({
    queryKey: ["environment", id],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace_id}/environments/{environment_id}",
          {
            params: { path: { workspace_id: workspaceId, environment_id: id } },
            signal,
          },
        )
        .then(representation),
  });
}
