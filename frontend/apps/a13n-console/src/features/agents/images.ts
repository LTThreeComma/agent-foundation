import type { Client } from "../../service-client";
import { ifMatch, representation } from "../../shared/api";

/** Replace an agent's avatar with `file`, or remove it; the agent's ETag guards either change. */
export function changeAgentImage(
  client: Client,
  workspaceId: string,
  agentId: string,
  etag: string,
  file: File | null,
) {
  const params = { path: { workspace_id: workspaceId, agent_id: agentId } };
  return file
    ? client.http
        .PUT("/api/v1/workspaces/{workspace_id}/agents/{agent_id}/avatar", {
          params,
          headers: { ...ifMatch(etag), "Content-Type": file.type },
          body: file,
        })
        .then(representation)
    : client.http
        .DELETE("/api/v1/workspaces/{workspace_id}/agents/{agent_id}/avatar", {
          params,
          headers: ifMatch(etag),
        })
        .then(representation);
}
