import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { EnvironmentInstances } from "./instances";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    organization: { id: "org_test" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../shared/page", () => ({
  PageActions: ({ children }: { children: React.ReactNode }) => children,
}));

const collections: Record<string, unknown[]> = {
  "/api/v1/provider-types/{kind}": [
    { type: "http_envd", display_name: "HTTP envd", supports_managed: false },
    { type: "docker", display_name: "Docker", supports_managed: true },
  ],
  "/api/v1/organizations/{organization_id}/environment-providers": [
    { id: "eprov_device", name: "My connection", type: "http_envd" },
    { id: "eprov_docker", name: "Docker host", type: "docker" },
  ].map((provider) => ({ ...provider, enabled: true })),
  "/api/v1/workspaces/{workspace_id}/environment-templates": [
    {
      id: "envtpl_python",
      name: "Python",
      description: null,
      enabled: true,
      version: 2,
    },
    {
      id: "envtpl_retired",
      name: "Retired",
      description: null,
      enabled: false,
      version: 1,
    },
  ],
};

beforeEach(() => {
  http.GET.mockImplementation(async (path: string) => ({
    data: { items: collections[path] ?? [], next_cursor: null },
  }));
  http.POST.mockReset().mockResolvedValue({ data: { id: "env_test" } });
});

async function openCreate() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <EnvironmentInstances />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Create environment" }));
  return { cache, user };
}

it("allocates a managed environment from an enabled template", async () => {
  const { cache, user } = await openCreate();
  await user.type(screen.getByRole("textbox", { name: "Name" }), "Scratch");
  await user.click(screen.getByRole("combobox", { name: "Template" }));
  const python = await screen.findByRole("option", { name: /Python/ });
  expect(screen.queryByRole("option", { name: /Retired/ })).toBeNull();
  await user.click(python);
  await user.click(screen.getByRole("button", { name: "Create environment" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
  expect(http.POST.mock.calls[0]).toEqual([
    "/api/v1/workspaces/{workspace_id}/environments",
    {
      params: { path: { workspace_id: "ws_test" } },
      body: { template_id: "envtpl_python", name: "Scratch" },
    },
  ]);
  cache.clear();
});

it("registers an http_envd device with a typed Device identity rather than raw state", async () => {
  const { cache, user } = await openCreate();
  await user.click(screen.getByRole("combobox", { name: "Ownership" }));
  await user.click(
    await screen.findByRole("option", { name: /^External target/ }),
  );
  await user.click(screen.getByRole("combobox", { name: "Provider" }));
  const device = await screen.findByRole("option", { name: /My connection/ });
  expect(screen.queryByRole("option", { name: /Docker host/ })).toBeNull();
  await user.click(device);
  expect(
    screen.queryByRole("textbox", {
      name: "Connection configuration (JSON)",
    }),
  ).toBeNull();
  await user.type(
    screen.getByRole("textbox", { name: "Device ID" }),
    "my-laptop",
  );
  await user.click(screen.getByRole("button", { name: "Create environment" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
  expect(http.POST.mock.calls[0]).toEqual([
    "/api/v1/workspaces/{workspace_id}/environments",
    expect.objectContaining({
      params: { path: { workspace_id: "ws_test" } },
      body: { provider_id: "eprov_device", device_id: "my-laptop" },
    }),
  ]);
  cache.clear();
});
