import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { expect, test, vi } from "vitest";
import { createClient } from "../../service-client";
import { i18n } from "../../i18n";
import type { useScope } from "../../layout/workspace";
import { WaitingPanel } from "./waiting";

let scope: ReturnType<typeof useScope>;
vi.mock("../../layout/workspace", () => ({ useScope: () => scope }));
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
function mount(fetcher: typeof fetch) {
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
      permissions: ["read", "run"],
    },
  };
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const submitted = vi.fn();
  const component = (busy = false, lastRunId = "waiting-run") => (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queries}>
        <WaitingPanel
          threadId="thread"
          runId="waiting-run"
          busy={busy}
          lastRunId={lastRunId}
          onSubmitted={submitted}
        />
      </QueryClientProvider>
    </I18nextProvider>
  );
  const view = render(component());
  return {
    submitted,
    update: (busy: boolean, lastRunId: string) =>
      view.rerender(component(busy, lastRunId)),
  };
}
const waiting = {
  status: "waiting",
  pending: [
    {
      tool_call_id: "call-approval",
      kind: "approval",
      tool_name: "send",
      arguments: { destination: "review" },
    },
    {
      tool_call_id: "call-client",
      kind: "client_tool",
      tool_name: "review",
      arguments: {},
    },
  ],
};

test("feedback carries exact target/results and reuses its key after a lost acknowledgement", async () => {
  const writes: Request[] = [];
  const { submitted } = mount(async (request) => {
    const input = request instanceof Request ? request : new Request(request);
    if (input.method === "GET") return json(waiting);
    writes.push(input.clone());
    if (writes.length === 1) throw new TypeError("Connection lost");
    return json({
      entry: { status: "consumed" },
      run: { id: "successor" },
      replayed: true,
    });
  });
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("checkbox", { name: "Approve this request" }),
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "Manual JSON result" }),
    { target: { value: '{"review":"ready"}' } },
  );
  await user.click(screen.getByRole("button", { name: "Submit response" }));
  await screen.findByText("Connection lost");
  await user.click(screen.getByRole("button", { name: "Submit response" }));
  await waitFor(() => expect(submitted).toHaveBeenCalledOnce());
  expect(writes).toHaveLength(2);
  expect(writes[0].headers.get("Idempotency-Key")).toBe(
    writes[1].headers.get("Idempotency-Key"),
  );
  expect(await writes[1].json()).toEqual({
    kind: "feedback",
    waiting_run_id: "waiting-run",
    answers: [
      { tool_call_id: "call-approval", action: "approve" },
      {
        tool_call_id: "call-client",
        action: "complete",
        result: { review: "ready" },
      },
    ],
  });
  expect(
    screen
      .getByRole("button", { name: "Submit response" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

test("empty response uses defaults; a failed successor allows an explicit new submission", async () => {
  const writes: Request[] = [];
  const { submitted, update } = mount(async (request) => {
    const input = request instanceof Request ? request : new Request(request);
    if (input.method === "GET") return json(waiting);
    writes.push(input.clone());
    return json({
      entry: { status: "assigned" },
      run: { id: `successor-${writes.length}` },
      replayed: false,
    });
  });
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Submit response" }),
  );
  await waitFor(() => expect(submitted).toHaveBeenCalledOnce());
  expect(await writes[0].json()).toEqual({
    kind: "feedback",
    waiting_run_id: "waiting-run",
    answers: [],
  });
  update(true, "waiting-run");
  update(false, "failed-successor");
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Submit response" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await user.click(screen.getByRole("button", { name: "Submit response" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[0].headers.get("Idempotency-Key")).not.toBe(
    writes[1].headers.get("Idempotency-Key"),
  );
});
