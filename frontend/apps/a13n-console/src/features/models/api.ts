import { providerApi, type ProviderScope } from "../providers/api";
import type { Client } from "../../service-client";
import {
  allPages,
  data,
  ifMatch,
  representation,
  type Schema,
} from "../../shared/api";
export type ModelScope = { kind: "workspace"; id: string };
/** The Workspace media understanding defaults, read by their settings section and by the Models list. */
export function mediaDefaultsQuery(client: Client, workspaceId: string) {
  return {
    queryKey: ["media-understanding-defaults", workspaceId],
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      client
        .workspace(workspaceId)
        .GET("/api/v1/media-understanding-defaults", {
          signal,
        })
        .then(representation),
  };
}
/** Models always belong to one workspace, including when their provider is shared. */
export function modelApi(
  client: Client,
  organizationId: string,
  scope: ModelScope,
) {
  const http = client.workspace(scope.id);
  const models = (
    signal: AbortSignal,
    cursor: string | undefined,
    limit = 30,
  ) =>
    http
      .GET("/api/v1/models", {
        params: {
          query: { cursor, limit },
        },
        signal,
      })
      .then(data);
  return {
    ...modelProviderApi(client, organizationId, scope),
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
    ) => {
      if (!query && !provider_id && enabled === undefined)
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
            (enabled === undefined || model.enabled === enabled),
        ),
        next_cursor: null,
      };
    },
    catalog: (signal: AbortSignal) =>
      http.GET("/api/v1/model-catalog", { signal }).then(data),
    model: (model_id: string, signal: AbortSignal) =>
      http
        .GET("/api/v1/models/{model_reference}", {
          params: { path: { model_reference: model_id } },
          signal,
        })
        .then(representation),
    createModel: (body: Schema["ModelCreate"]) =>
      http
        .POST("/api/v1/models", {
          body,
        })
        .then(data),
    updateModel: (
      model_id: string,
      etag: string,
      body: Schema["ModelUpdate"],
    ) =>
      http
        .PATCH("/api/v1/models/{model_reference}", {
          params: { path: { model_reference: model_id } },
          headers: ifMatch(etag),
          body,
        })
        .then(data),
  };
}

export function modelProviderApi(
  client: Client,
  organizationId: string,
  scope: ProviderScope,
) {
  return providerApi(client, organizationId, scope, "model");
}
