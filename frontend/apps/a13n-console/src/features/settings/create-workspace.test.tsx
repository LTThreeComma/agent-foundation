// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { CreateWorkspace } from "./create-workspace";

const http = vi.hoisted(() => ({ POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function Location() {
  return <output aria-label="Current path">{useLocation().pathname}</output>;
}

it("names a new workspace and derives its URL key from the name", async () => {
  const user = userEvent.setup();
  http.POST.mockResolvedValue({
    data: { id: "ws_new", key: "product-design", name: "Product Design" },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <Location />
        <CreateWorkspace organizationId="org_acme" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Create workspace" }));
  expect(screen.queryByRole("textbox", { name: "URL key" })).toBeNull();
  await user.type(
    screen.getByRole("textbox", { name: "Workspace name" }),
    "Product Design",
  );
  await user.click(
    screen.getAllByRole("button", { name: "Create workspace" }).at(-1)!,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).toBe(
      "/workspace/product-design/settings",
    ),
  );
  expect(http.POST).toHaveBeenCalledWith(
    "/api/v1/organizations/{organization_id}/workspaces",
    {
      params: { path: { organization_id: "org_acme" } },
      body: { name: "Product Design", key: "product-design" },
    },
  );
});
