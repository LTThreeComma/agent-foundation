import type { Client } from "../../service-client";
import { data, type Schema } from "../../shared/api";

type Connection = Schema["Connection"];

export function connectionPath(connection: Connection) {
  return {
    workspace_id: connection.workspace_id,
    connection_id: connection.id,
  };
}

/** A disabled connection keeps its credential state; the collection shows it as disabled. */
export function connectionState(connection: Connection) {
  return connection.enabled ? connection.status : "disabled";
}

/** Discovers the tools with the current configuration and credential; the outcome becomes `last_test`. */
export function testConnection(client: Client, connection: Connection) {
  return client.http
    .POST(
      "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/test",
      {
        params: { path: connectionPath(connection) },
      },
    )
    .then(data);
}

/** A failed test carries safe text explaining why the tools could not be listed. */
export function requireTestSuccess(test: Schema["ConnectionTest"]) {
  if (test.status === "failed") throw new Error(test.message ?? test.status);
  return test;
}

/** What revoking did beyond clearing the local credential. */
export type RemoteCleanup =
  Schema["RevokedConnection"]["remote_revocation"] | "unknown";
export function revokeCleanup(
  revoked: Schema["RevokedConnection"],
): RemoteCleanup {
  // A failed remote revoke is recorded as the connection's failure; one whose
  // outcome is unknown may still have taken effect.
  return revoked.remote_revocation === "failed" &&
    revoked.failure?.reason === "outcome_unknown"
    ? "unknown"
    : revoked.remote_revocation;
}
