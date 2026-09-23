import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { expect, test, vi } from "vitest";
import { createClient, type components } from "../../service-client";
import { i18n } from "../../i18n";
import type { useScope } from "../../layout/workspace";
import { ConnectionEditor } from "./editor";

let scope: ReturnType<typeof useScope>;
vi.mock("../../layout/workspace", () => ({ useScope: () => scope }));

test("a refused OAuth probe refreshes private status and revocation clears recovery advice", async () => {
  let status = "active";
  const requests: string[] = [];
  const connection: components["schemas"]["ConnectionView"] = {
    id: "conn_oauth",
    organization_id: "org",
    workspace_id: "ws",
    type: "mcp",
    name: "Personal",
    auth: "oauth",
    enabled: true,
    credential_configured: false,
    version: 1,
    config: {
      url: "https://tools.example/mcp",
      oauth: {
        issuer: "https://auth.example",
        client_id: "client",
        scopes: ["tools"],
        token_endpoint_auth_method: "none",
      },
    },
  };
  scope = {
    client: createClient({
      baseUrl: "https://service.test",
      auth: { type: "session", csrfToken: "fixture" },
      maxReadRetries: 0,
      fetch: async (input) => {
        const request = input instanceof Request ? input : new Request(input);
        const endpoint = new URL(request.url).pathname.split("/").at(-1);
        requests.push(`${request.method} ${endpoint}`);
        let body: unknown = connection;
        let code = 200;
        if (endpoint === "test") {
          status = "reauthorization_required";
          code = 422;
          body = {
            error: {
              code: "disabled",
              message: "Reconnect this OAuth Connection before using its tools",
            },
          };
        } else if (endpoint === "authorization" || endpoint === "revoke") {
          if (endpoint === "revoke") status = "revoked";
          body = {
            id: "cauth_fixture",
            connection_id: connection.id,
            status,
            generation: 3,
            failure: status === "active" ? null : "unknown_after_dispatch",
            operation_kind: null,
            expires_at: null,
          };
        }
        return new Response(JSON.stringify(body), {
          status: code,
          headers: {
            "Content-Type": "application/json",
            ETag: '"conn_oauth:1"',
          },
        });
      },
    }),
    cache: ["principal", "ws"],
    path: { workspace_id: "ws" },
    base: "/workspace/ws",
    workspace: {
      id: "ws",
      organization_id: "org",
      key: "ws",
      name: "Workspace",
      version: 1,
      permissions: ["read", "write", "run"],
    },
  };
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queries}>
        <ConnectionEditor
          id={connection.id}
          onClose={() => {}}
          onSaved={async () => {}}
        />
      </QueryClientProvider>
    </I18nextProvider>,
  );
  await screen.findByText("Your account is connected");
  const name = screen.getByRole("textbox", { name: "Name" });
  fireEvent.change(name, { target: { value: "Unsaved name" } });
  expect(
    screen
      .getByRole("button", { name: "Reconnect account" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen.getByText("Save connection changes before connecting your account."),
  ).toBeTruthy();
  fireEvent.change(name, { target: { value: "Personal" } });
  expect(
    screen
      .getByRole("button", { name: "Reconnect account" })
      .hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.click(
    await screen.findByRole("button", { name: "Test connection" }),
  );
  await screen.findByText("Reconnect required");
  expect(requests.filter((request) => request === "POST test")).toHaveLength(1);
  expect(
    screen.getByText(
      /an uncertain token request will not be retried automatically/,
    ),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Disconnect account" }));
  await screen.findByText("Your account is not connected");
  await waitFor(() =>
    expect(
      screen.queryByText(
        /an uncertain token request will not be retried automatically/,
      ),
    ).toBeNull(),
  );
  queries.clear();
});
