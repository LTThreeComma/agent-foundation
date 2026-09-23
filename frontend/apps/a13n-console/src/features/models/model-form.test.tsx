import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ModelEditor } from "./model-editor";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  close: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { GET: state.GET, POST: state.POST, PATCH: state.PATCH },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../layout/workspace", () => ({
  useAccess: () => ({
    organization: { id: "org_test" },
    workspace: { key: "workspace-test" },
  }),
}));
const provider = {
  id: "mprov_test",
  name: "My endpoint",
  type: "openai",
  enabled: true,
  workspace_id: "ws_test",
  config: { base_url: "https://example.com/v1" },
};
const definition = {
  type: "openai",
  display_name: "OpenAI",
  model_apis: ["openai.chat_completions", "openai.responses"],
  default_model_api: "openai.chat_completions",
  model_api_labels: {
    "openai.chat_completions": "Chat Completions",
    "openai.responses": "Responses",
  },
  supports_test: true,
  setup_url: null,
  setup_label: null,
  authentication: { mode: "required" },
  credential_schema: {
    type: "string",
    "x-a13n-credential-format": "api_key",
  },
  configuration_schema: {
    type: "object",
    properties: { base_url: { type: "string" }, auth_mode: { type: "string" } },
  },
};
const pricing = (input: string, output: string) => ({
  provider: "openai",
  model: "gpt",
  source: "genai_prices",
  source_revision: "2026-09-01",
  rules: [
    {
      rule_id: "standard",
      constraint: { kind: "always" },
      prices: [
        { price_key: "input_mtok", price: input },
        { price_key: "output_mtok", price: output },
      ],
    },
  ],
});
const entry = {
  key: "openai:gpt-5.5",
  model_name: "gpt-5.5",
  characteristics: {
    capabilities: ["image_understanding"],
    context_window_tokens: 100000,
  },
  pricing: pricing("5", "30"),
  source_url: "https://example.com/gpt-5.5",
};
const secondEntry = {
  key: "openai:gpt-5.6",
  model_name: "gpt-5.6",
  characteristics: { capabilities: [], context_window_tokens: 200000 },
  pricing: pricing("4", "24"),
  source_url: "https://example.com/gpt-5.6",
};
const model = {
  id: "mdl_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  provider_id: "mprov_test",
  key: "smart",
  name: "Smart",
  description: "Company gateway model",
  config: {
    model_name: "company-smart",
    model_api: "openai.responses",
    characteristics: { capabilities: ["image_understanding"] },
    max_tokens: 4096,
  },
  pricing: pricing("1", "2"),
  enabled: true,
  version: 3,
};
const response = () =>
  new Response(null, { headers: { ETag: '"mdl_test:3"' } });
function mount(ids: { providerId?: string; modelId?: string } = {}) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter>
        <ModelEditor
          scope={{ kind: "workspace", id: "ws_test" }}
          {...ids}
          controlledOpen
          onClose={state.close}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const modelsPath = "/api/v1/organizations/{organization_id}/models";

beforeEach(() => {
  state.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/provider-types/{kind}"
        ? { items: [definition], next_cursor: null }
        : path.endsWith("/catalog")
          ? { items: [entry, secondEntry], next_cursor: null }
          : path.endsWith("{model_id}")
            ? model
            : { items: [provider], next_cursor: null },
    response: response(),
  }));
  state.POST.mockImplementation(
    async (_path: string, args: { body?: unknown }) => ({
      data: { id: "mdl_test", ...(args.body as object) },
      response: response(),
    }),
  );
  state.PATCH.mockResolvedValue({ data: model, response: response() });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("creates a manual model with JSON request defaults in its configuration", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Custom model/ }));
  await user.type(
    await screen.findByLabelText("Upstream model"),
    "company-smart",
  );
  await user.type(screen.getByLabelText("Name"), "Smart");
  await user.type(screen.getByLabelText("Model key"), "smart");
  fireEvent.change(screen.getByLabelText("Description"), {
    target: { value: "Company gateway" },
  });
  await user.click(screen.getByRole("switch", { name: "Enabled" }));
  expect(screen.queryByLabelText("Thinking effort")).toBeNull();
  expect(screen.queryByLabelText("Max output tokens")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.change(screen.getByLabelText("Settings JSON"), {
    target: { value: '{"max_tokens":4096,"temperature":0.2}' },
  });
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(modelsPath, {
      params: { path: { organization_id: "org_test" } },
      body: {
        workspace_id: "ws_test",
        provider_id: "mprov_test",
        key: "smart",
        name: "Smart",
        description: "Company gateway",
        enabled: false,
        config: {
          max_tokens: 4096,
          temperature: 0.2,
          model_name: "company-smart",
          model_api: "openai.chat_completions",
          characteristics: {},
        },
        pricing: null,
      },
    }),
  );
});

it("rejects request defaults that are not a JSON object", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Custom model/ }));
  await user.type(
    await screen.findByLabelText("Upstream model"),
    "company-smart",
  );
  await user.type(screen.getByLabelText("Name"), "Smart");
  await user.type(screen.getByLabelText("Model key"), "smart");
  await user.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.change(screen.getByLabelText("Settings JSON"), {
    target: { value: "[4096]" },
  });
  await user.click(screen.getByRole("button", { name: "Add model" }));
  expect(await screen.findByText("Enter a JSON object.")).toBeTruthy();
  expect(state.POST).not.toHaveBeenCalled();
});

it("keeps the catalogue price for an edited gateway upstream ID", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /gpt-5\.5/ }));
  await user.clear(await screen.findByLabelText("Upstream model"));
  await user.type(screen.getByLabelText("Upstream model"), "company-smart");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      modelsPath,
      expect.objectContaining({
        body: expect.objectContaining({
          key: "gpt-5-5",
          config: expect.objectContaining({
            model_name: "company-smart",
            characteristics: entry.characteristics,
          }),
          pricing: { ...entry.pricing, model: "company-smart" },
        }),
      }),
    ),
  );
  expect(state.POST.mock.calls[0][1].body).not.toHaveProperty("catalog_key");
});

it("applies a newly selected model immediately and creates it from the catalogue", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /gpt-5\.5/ }));
  await user.click(
    await screen.findByRole("button", { name: "Choose a model" }),
  );
  await user.click(await screen.findByRole("button", { name: /gpt-5\.6/ }));
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("gpt-5.6");
  expect(
    (screen.getByLabelText("Context window") as HTMLInputElement).value,
  ).toBe("200000");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(modelsPath, {
      params: { path: { organization_id: "org_test" } },
      body: {
        workspace_id: "ws_test",
        provider_id: "mprov_test",
        key: "gpt-5-5",
        name: "gpt-5.5",
        description: "",
        enabled: true,
        catalog_key: secondEntry.key,
      },
    }),
  );
});

it("saves an edited model under its ETag without offering a billable test", async () => {
  mount({ modelId: "mdl_test" });
  const user = userEvent.setup();
  const name = await screen.findByLabelText("Name");
  expect(screen.queryByRole("button", { name: "Check connection" })).toBeNull();
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("company-smart");
  expect(
    (await screen.findByRole("combobox", { name: "API" })).textContent,
  ).toBe("Responses");
  await user.clear(name);
  await user.type(name, "Smarter");
  await user.click(screen.getByRole("switch", { name: "Enabled" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(state.PATCH).toHaveBeenCalledWith(`${modelsPath}/{model_id}`, {
      params: {
        path: { organization_id: "org_test", model_id: "mdl_test" },
      },
      headers: { "If-Match": '"mdl_test:3"' },
      body: {
        name: "Smarter",
        description: "Company gateway model",
        enabled: false,
        config: {
          max_tokens: 4096,
          model_name: "company-smart",
          model_api: "openai.responses",
          characteristics: { capabilities: ["image_understanding"] },
        },
        pricing: { ...model.pricing, model: "company-smart" },
      },
    }),
  );
});
