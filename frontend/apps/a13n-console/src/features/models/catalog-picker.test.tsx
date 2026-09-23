import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { CatalogPicker } from "./catalog-picker";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const entry: Schema["CatalogModel"] = {
  key: "openai:gpt-5.5",
  model_name: "gpt-5.5",
  characteristics: {},
  pricing: null,
  source_url: "https://example.com/models/gpt-5.5",
};
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("keeps a large catalog searchable without rendering every model at once", async () => {
  const many = Array.from({ length: 150 }, (_, index) => {
    const name = `model-${String(index).padStart(3, "0")}`;
    return { ...entry, key: `openai:${name}`, model_name: name };
  });
  const selected = vi.fn();
  render(<CatalogPicker entries={many} value={null} onSelect={selected} />);
  const user = userEvent.setup();
  expect(screen.queryByRole("button", { name: /model-149/ })).toBeNull();
  await user.type(screen.getByRole("searchbox"), "model-149");
  await user.click(await screen.findByRole("button", { name: /model-149/ }));
  expect(selected).toHaveBeenCalledWith(many[149]);
});

it("marks the catalogue entry the draft was seeded from", () => {
  render(
    <CatalogPicker
      entries={[entry]}
      providerName="OpenAI"
      value={entry.key}
      onSelect={vi.fn()}
    />,
  );
  expect(
    screen.getByRole("button", { name: /gpt-5\.5/ }).textContent,
  ).toContain("Selected");
});

it("offers a custom model when the catalog does not list one", async () => {
  const select = vi.fn();
  render(<CatalogPicker entries={[entry]} value={null} onSelect={select} />);
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: /Custom model/ }));
  expect(select).toHaveBeenCalledWith(null);
});
