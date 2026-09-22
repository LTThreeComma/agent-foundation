import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { expect, test, vi } from "vitest";
import { createClient, type components } from "../../service-client";
import { i18n } from "../../i18n";
import { Composer } from "./composer";
import type { useScope } from "../../layout/workspace";

let scope: ReturnType<typeof useScope>;
vi.mock("../../layout/workspace", () => ({ useScope: () => scope }));
function mount(
  fetcher: typeof fetch,
  permissions: components["schemas"]["Verb"][] = ["read", "run"],
) {
  scope = {
    client: createClient({
      baseUrl: "https://service.test",
      auth: { type: "session", csrfToken: "proof" },
      fetch: fetcher,
      maxReadRetries: 0,
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
      permissions,
    },
  };
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const submitted = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queries}>
        <Composer onSubmitted={submitted} onInterrupted={vi.fn()} />
      </QueryClientProvider>
    </I18nextProvider>,
  );
  return submitted;
}
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });

test("IME and Shift+Enter do not submit; uncertain same-intent retry keeps its idempotency key", async () => {
  const mutations: Request[] = [];
  const submitted = mount(async (request) => {
    const input = request instanceof Request ? request : new Request(request);
    if (input.method === "GET")
      return json({
        items: [{ id: "agent", name: "Assistant" }],
        next_cursor: null,
      });
    mutations.push(input);
    if (mutations.length === 1) throw new TypeError("Connection lost");
    return json({
      thread_id: "thread",
      session_id: "session",
      entry: {},
      run: null,
      replayed: true,
    });
  });
  const user = userEvent.setup();
  const textarea = screen.getByRole("textbox", { name: "Message" });
  await user.type(textarea, "Hello");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.compositionStart(textarea);
  fireEvent.keyDown(textarea, {
    key: "Enter",
    code: "Enter",
    isComposing: true,
  });
  fireEvent.compositionEnd(textarea);
  fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", shiftKey: true });
  expect(mutations).toHaveLength(0);
  await user.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Connection lost");
  expect((textarea as HTMLTextAreaElement).value).toBe("Hello");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(submitted).toHaveBeenCalledOnce());
  expect(mutations).toHaveLength(2);
  expect(mutations[0].headers.get("Idempotency-Key")).toBe(
    mutations[1].headers.get("Idempotency-Key"),
  );
  expect(mutations[1].headers.get("X-CSRF-Token")).toBe("proof");
  expect((textarea as HTMLTextAreaElement).value).toBe("");
});

test("viewer has no message or interruption controls", () => {
  mount(async () => json({ items: [], next_cursor: null }), ["read"]);
  expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  expect(
    screen.getByText("View only. A runner role is required to send messages."),
  ).toBeTruthy();
});
