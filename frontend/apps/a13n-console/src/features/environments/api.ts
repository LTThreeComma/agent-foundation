import { queryOptions } from "@tanstack/react-query";
import type { Client } from "../../service-client";
import { data, representation, type Schema } from "../../shared/api";
import { providerApi } from "../providers/api";
export type EnvironmentScope = {
  kind: "workspace" | "organization";
  id: string;
};
/** Templates belong to one workspace. */
export type WorkspaceScope = EnvironmentScope & { kind: "workspace" };
/** The organization's environment providers as the scope sees them. */
export function environmentApi(
  client: Client,
  organizationId: string,
  scope: EnvironmentScope,
) {
  return providerApi(client, organizationId, scope, "environment");
}

export function environmentTemplates(
  client: Client,
  workspaceId: string,
  signal: AbortSignal,
  cursor?: string,
) {
  return client
    .workspace(workspaceId)
    .GET("/api/v1/environment-templates", {
      params: { query: { cursor } },
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
  body: Schema["ManagedEnvironmentInput"],
) {
  return client
    .workspace(workspaceId)
    .POST("/api/v1/environments", {
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
      client
        .workspace(workspaceId)
        .GET("/api/v1/environments/{environment_id}", {
          params: { path: { environment_id: id } },
          signal,
        })
        .then(representation),
  });
}
