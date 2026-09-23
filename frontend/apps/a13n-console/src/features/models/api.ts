import type { Client } from "../../service-client";
import {
  allPages,
  data,
  ifMatch,
  representation,
  type Schema,
} from "../../shared/api";
export type ModelScope = { kind: "workspace" | "organization"; id: string };
/** The Workspace media understanding defaults, read by their settings section and by the Models list. */
export function mediaDefaultsQuery(client: Client, workspaceId: string) {
  return {
    queryKey: ["media-understanding-defaults", workspaceId],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/media-understanding-defaults", {
          params: { path: { workspace_id: workspaceId } },
          signal,
        })
        .then(representation),
  };
}
/**
 * Models and their providers live in the organization collection: a workspace
 * lists its own and the shared ones and creates its own; the organization
 * creates shared ones.
 */
export function modelApi(
  client: Client,
  organizationId: string,
  scope: ModelScope,
) {
  const organization_id = organizationId,
    workspace_id = scope.kind === "workspace" ? scope.id : null;
  const models = (
    signal: AbortSignal,
    cursor: string | undefined,
    limit = 30,
  ) =>
    client.http
      .GET("/api/v1/organizations/{organization_id}/models", {
        params: {
          path: { organization_id },
          query: { workspace_id, cursor, limit },
        },
        signal,
      })
      .then(data);
  return {
    /**
     * The collection has no search or filters, so a filtered view reads it
     * whole and matches in the browser.
     */
    models: async (
      signal: AbortSignal,
      cursor?: string,
      query?: string,
      provider_id?: string,
      enabled?: boolean,
      owner_scope?: "organization" | "workspace",
    ) => {
      if (!query && !provider_id && enabled === undefined && !owner_scope)
        return models(signal, cursor);
      const term = query?.toLocaleLowerCase();
      const items = await allPages((next) => models(signal, next, 100));
      return {
        items: items.filter(
          (model) =>
            (!term ||
              `${model.name} ${model.key} ${model.config.model_name}`
                .toLocaleLowerCase()
                .includes(term)) &&
            (!provider_id || model.provider_id === provider_id) &&
            (enabled === undefined || model.enabled === enabled) &&
            (!owner_scope ||
              (owner_scope === "workspace") === (model.workspace_id !== null)),
        ),
        next_cursor: null,
      };
    },
    providers: (signal: AbortSignal, cursor?: string) =>
      client.http
        .GET("/api/v1/organizations/{organization_id}/model-providers", {
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
          "/api/v1/organizations/{organization_id}/model-providers/{provider_id}",
          { params: { path: { organization_id, provider_id } }, signal },
        )
        .then(representation),
    createProvider: (body: Omit<Schema["ProviderCreate"], "workspace_id">) =>
      client.http
        .POST("/api/v1/organizations/{organization_id}/model-providers", {
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
          "/api/v1/organizations/{organization_id}/model-providers/{provider_id}",
          {
            params: { path: { organization_id, provider_id } },
            headers: ifMatch(etag),
            body,
          },
        )
        .then(data),
    testProvider: (provider_id: string) =>
      client.http
        .POST(
          "/api/v1/organizations/{organization_id}/model-providers/{provider_id}/test",
          { params: { path: { organization_id, provider_id } } },
        )
        .then(data),
    catalog: (provider_id: string, signal: AbortSignal) =>
      client.http
        .GET(
          "/api/v1/organizations/{organization_id}/model-providers/{provider_id}/catalog",
          { params: { path: { organization_id, provider_id } }, signal },
        )
        .then(data),
    model: (model_id: string, signal: AbortSignal) =>
      client.http
        .GET("/api/v1/organizations/{organization_id}/models/{model_id}", {
          params: { path: { organization_id, model_id } },
          signal,
        })
        .then(representation),
    createModel: (body: Omit<Schema["ModelCreate"], "workspace_id">) =>
      client.http
        .POST("/api/v1/organizations/{organization_id}/models", {
          params: { path: { organization_id } },
          body: { ...body, workspace_id },
        })
        .then(data),
    updateModel: (
      model_id: string,
      etag: string,
      body: Schema["ModelUpdate"],
    ) =>
      client.http
        .PATCH("/api/v1/organizations/{organization_id}/models/{model_id}", {
          params: { path: { organization_id, model_id } },
          headers: ifMatch(etag),
          body,
        })
        .then(data),
  };
}
