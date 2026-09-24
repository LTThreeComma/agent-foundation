import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { modelApi } from "../models/api";

export function useAgentChoices() {
  const client = useClient(),
    { workspace, organization } = useWorkspace();
  return useQuery({
    queryKey: ["agent-choices", workspace.id],
    queryFn: async ({ signal }) => {
      const path = { workspace_id: workspace.id };
      const [models, skills, connections] = await Promise.all([
        modelApi(client, organization.id, {
          kind: "workspace",
          id: workspace.id,
        })
          .models(signal, undefined, undefined, undefined, true)
          .then((page) => page.items),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/skills", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/connections", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
      ]);
      return { models, skills, connections };
    },
  });
}
