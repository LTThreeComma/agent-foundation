// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import {
  clearMCPAuthorization,
  readMCPAuthorization,
  saveMCPAuthorization,
  takeMCPCallback,
} from "./authorization-context";

afterEach(() => {
  clearMCPAuthorization();
  window.history.replaceState(null, "", "/");
});
const state = "s".repeat(48);
const connection = {
  id: "mcpc_test",
  workspace_id: "ws_test",
} as Schema["MCPConnection"];
function launch(expiresAt = Date.now() + 60_000) {
  return {
    authorization_url: `https://auth.example/authorize?state=${state}`,
    expires_at: new Date(expiresAt).toISOString(),
  } as Schema["MCPAuthorizationLaunch"];
}
it("binds the same-tab callback to its exact workspace and connection, excluding the authorization code", () => {
  saveMCPAuthorization(launch(), connection, "/workspace/design");
  expect(readMCPAuthorization()).toMatchObject({
    state,
    connectionId: "mcpc_test",
    workspaceId: "ws_test",
    returnPath: "/workspace/design/connections",
  });
  window.history.replaceState(
    null,
    "",
    `/mcp-setup/callback?code=private-code&state=${state}&iss=https://auth.example`,
  );
  expect(takeMCPCallback()).toEqual({
    code: "private-code",
    state,
    issuer: "https://auth.example",
  });
  expect(window.location.search).toBe("");
  expect(JSON.stringify(sessionStorage)).not.toContain("private-code");
  expect(takeMCPCallback()).toBeNull();
});
it("rejects expired context, unsafe return paths and ambiguous callback values", () => {
  saveMCPAuthorization(launch(Date.now() - 1), connection, "/workspace/design");
  expect(readMCPAuthorization()).toBeNull();
  saveMCPAuthorization(launch(), connection, "https://evil.example");
  expect(readMCPAuthorization()).toBeNull();
  window.history.replaceState(
    null,
    "",
    `/mcp-setup/callback?code=one&code=two&state=${state}&iss=https://auth.example`,
  );
  expect(takeMCPCallback()).toBeNull();
  expect(window.location.search).toBe("");
});
