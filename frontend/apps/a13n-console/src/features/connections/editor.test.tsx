import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { expect, test, vi } from "vitest";
import { createClient, type components } from "../../service-client";
import { i18n } from "../../i18n";
import type { useScope } from "../../layout/workspace";
import { ConnectionEditor } from "./editor";

type Connection = components["schemas"]["ConnectionView"];
let scope: ReturnType<typeof useScope>;
vi.mock("../../layout/workspace", () => ({ useScope: () => scope }));
const key = ["user", "workspace", "connection", "conn_proof"];
function setup() {
  let current: Connection = {
    id: "conn_proof",
    organization_id: "organization",
    workspace_id: "workspace",
    type: "mcp",
    name: "Original",
    config: {
      url: "https://old.example/mcp",
      tools: ["increment"],
      recovery_retry_safe_tools: ["increment"],
    },
    auth: "bearer",
    credential_configured: true,
    enabled: true,
    version: 1,
  };
  const writes: {
    etag: string | null;
    body: components["schemas"]["ConnectionUpdate"];
  }[] = [];
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const collection = ["user", "workspace", "connections", null];
  queries.setQueryData(collection, { items: [current], next_cursor: null });
  const saved = vi.fn();
  scope = {
    client: createClient({
      baseUrl: "https://service.test",
      auth: { type: "session", csrfToken: "proof" },
      maxReadRetries: 0,
      fetch: async (input) => {
        const request = input instanceof Request ? input : new Request(input);
        if (request.method === "PATCH") {
          const body = await request.json();
          writes.push({ etag: request.headers.get("if-match"), body });
          if (
            request.headers.get("if-match") !==
            `"conn_proof:${current.version}"`
          )
            return new Response(
              JSON.stringify({
                error: {
                  code: "conflict",
                  message: "Connection changed; reopen before saving.",
                },
              }),
              { status: 409, headers: { "Content-Type": "application/json" } },
            );
          const { credential: _credential, ...fields } = body;
          current = { ...current, ...fields, version: current.version + 1 };
        }
        return new Response(JSON.stringify(current), {
          headers: {
            "Content-Type": "application/json",
            ETag: `"conn_proof:${current.version}"`,
          },
        });
      },
    }),
    cache: ["user", "workspace"],
    path: { workspace_id: "workspace" },
    base: "/workspace/workspace",
    workspace: {
      id: "workspace",
      organization_id: "organization",
      name: "Workspace",
      key: "workspace",
      version: 1,
      permissions: ["read", "write"],
    },
  };
  function EditorSession() {
    const [open, setOpen] = useState(true);
    return open ? (
      <ConnectionEditor
        id="conn_proof"
        onClose={() => setOpen(false)}
        onSaved={async () => {
          saved();
          setOpen(false);
        }}
      />
    ) : (
      <button onClick={() => setOpen(true)}>Reopen</button>
    );
  }
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queries}>
        <EditorSession />
      </QueryClientProvider>
    </I18nextProvider>,
  );
  return {
    queries,
    collection,
    writes,
    saved,
    current: () => current,
    replace: (value: Connection) => {
      current = value;
    },
  };
}
const input = (name: string) =>
  screen.getByRole("textbox", { name }) as HTMLInputElement;

test.each([false, true])(
  "dirty draft retains its baseline after refetch and receives a real client conflict (reconfirm=%s)",
  async (reconfirm) => {
    const state = setup();
    await screen.findByRole("textbox", { name: "Name" });
    fireEvent.change(input("Name"), { target: { value: "My draft" } });
    fireEvent.change(input("Endpoint URL"), {
      target: { value: "https://new.example/mcp" },
    });
    if (reconfirm)
      fireEvent.click(
        screen.getByRole("checkbox", { name: /Reconfirm retry safety/ }),
      );
    state.replace({
      ...state.current(),
      version: 2,
      name: "Other writer",
      config: { ...state.current().config, url: "https://new.example/mcp" },
    });
    await state.queries.invalidateQueries({ queryKey: key });
    await waitFor(() =>
      expect(
        state.queries.getQueryData<{ value: Connection }>(key)?.value.version,
      ).toBe(2),
    );
    expect(input("Name").value).toBe("My draft");
    expect(
      screen.getByRole("checkbox", { name: /Reconfirm retry safety/ }),
    ).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await screen.findByText("Connection changed; reopen before saving.");
    expect(state.writes[0].etag).toBe('"conn_proof:1"');
    expect(state.writes[0].body.config).toMatchObject({
      recovery_retry_safe_tools: reconfirm ? ["increment"] : [],
    });
    expect(state.saved).not.toHaveBeenCalled();
    expect(state.current().name).toBe("Other writer");
    expect(input("Name").value).toBe("My draft");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(input("Name").value).toBe("Other writer"));
    expect(input("Endpoint URL").value).toBe("https://new.example/mcp");
  },
);

test("save clears credentials, updates caches, and reopens with current values and ETag", async () => {
  const state = setup();
  await screen.findByRole("textbox", { name: "Name" });
  fireEvent.change(input("Name"), { target: { value: "Saved name" } });
  fireEvent.click(screen.getByRole("button", { name: "Replace credential" }));
  fireEvent.change(screen.getByLabelText("Bearer token"), {
    target: { value: "new-secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
  await screen.findByRole("button", { name: "Reopen" });
  expect(state.writes[0].body.credential).toEqual({ token: "new-secret" });
  expect(state.writes[0].body.config).toMatchObject({
    recovery_retry_safe_tools: [],
  });
  expect(
    state.queries.getQueryData<{ value: Connection; etag: string }>(key),
  ).toEqual({ value: state.current(), etag: '"conn_proof:2"' });
  expect(state.queries.getQueryState(state.collection)?.isInvalidated).toBe(
    true,
  );
  // Another writer changes the resource while this editor is closed.
  state.replace({ ...state.current(), version: 3, name: "Latest name" });
  fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
  await waitFor(() => expect(input("Name").value).toBe("Latest name"));
  expect(screen.queryByLabelText("Bearer token")).toBeNull();
  expect(screen.getByText("Credential saved")).toBeDefined();
  fireEvent.change(input("Name"), { target: { value: "Second edit" } });
  fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
  await waitFor(() => expect(state.saved).toHaveBeenCalledTimes(2));
  expect(state.writes[1].etag).toBe('"conn_proof:3"');
  expect(state.writes[1].body.credential).toBeUndefined();
});
