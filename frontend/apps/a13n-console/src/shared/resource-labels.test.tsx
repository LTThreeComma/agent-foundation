// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  LabelOverridesField,
  ResourceLabels,
  ResourceLabelsDialog,
} from "./resource-labels";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

afterEach(cleanup);

it("adds, removes, clears, validates, and saves a label draft", async () => {
  const save = vi.fn();
  const user = userEvent.setup();
  render(
    <ResourceLabels
      labels={{ team: "platform" }}
      editable
      save={save}
      refresh={vi.fn()}
    />,
  );

  await user.click(screen.getByRole("button", { name: "Add label" }));
  const keys = screen.getAllByLabelText("Label key");
  const values = screen.getAllByLabelText("Label value");
  await user.type(keys[1], "stage");
  await user.type(values[1], "prod");
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  expect(save).toHaveBeenCalledWith({ team: "platform", stage: "prod" });

  await user.clear(keys[1]);
  await user.type(keys[1], "bad key");
  expect(screen.getByText("Enter a valid label key.")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Save labels" })
      .hasAttribute("disabled"),
  ).toBe(true);

  await user.click(screen.getByRole("button", { name: "Clear labels" }));
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  expect(save).toHaveBeenLastCalledWith({});
});

it("previews snapshot inheritance with explicit child overrides", async () => {
  const user = userEvent.setup();
  function Example() {
    const [labels, setLabels] = useState<Record<string, string>>({
      team: "runtime",
    });
    return (
      <LabelOverridesField
        value={labels}
        inherited={{ team: "platform", region: "cn" }}
        onChange={setLabels}
      />
    );
  }

  render(<Example />);
  expect(screen.getByText("team").parentElement?.textContent).toBe(
    "team=runtime",
  );
  expect(screen.getByText("region").parentElement?.textContent).toBe(
    "region=cn",
  );
  await user.click(screen.getByRole("button", { name: "Remove" }));
  expect(screen.getByText("team").parentElement?.textContent).toBe(
    "team=platform",
  );
});

it("preserves an edited draft after a label conflict until refresh", async () => {
  const labels = { team: "platform" };
  const save = vi.fn();
  const refresh = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(
    <ResourceLabels labels={labels} editable save={save} refresh={refresh} />,
  );
  const value = screen.getByLabelText("Label value");
  await user.clear(value);
  await user.type(value, "runtime");
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  rerender(
    <ResourceLabels
      labels={labels}
      editable
      error={new Error("This resource changed")}
      save={save}
      refresh={refresh}
    />,
  );

  expect((screen.getByLabelText("Label value") as HTMLInputElement).value).toBe(
    "runtime",
  );
  expect(screen.getByText("This resource changed")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Refresh labels" }));
  expect(refresh).toHaveBeenCalledOnce();
});

it("keeps duplicate override rows visible and invalid without collapsing the map", async () => {
  const user = userEvent.setup();
  function Example() {
    const [labels, setLabels] = useState<Record<string, string>>({
      team: "platform",
      stage: "prod",
    });
    const [valid, setValid] = useState(true);
    return (
      <>
        <LabelOverridesField
          value={labels}
          onChange={setLabels}
          onValidityChange={setValid}
        />
        <output aria-label="Saved labels">{JSON.stringify(labels)}</output>
        <output aria-label="Valid">{String(valid)}</output>
      </>
    );
  }

  render(<Example />);
  const keys = screen.getAllByLabelText("Label key");
  await user.clear(keys[1]);
  await user.type(keys[1], "team");
  expect(screen.getAllByDisplayValue("team")).toHaveLength(2);
  expect(screen.getByText("Label keys must be unique.")).toBeTruthy();
  expect(screen.getByLabelText("Valid").textContent).toBe("false");
  expect(
    Object.keys(JSON.parse(screen.getByLabelText("Saved labels").textContent!)),
  ).toHaveLength(2);
});

it("counts Unicode scalars instead of UTF-16 units in label values", () => {
  render(
    <ResourceLabels
      labels={{ note: "" }}
      editable
      save={vi.fn()}
      refresh={vi.fn()}
    />,
  );
  const value = screen.getByLabelText("Label value");
  fireEvent.change(value, { target: { value: "😀".repeat(256) } });
  expect(screen.queryByText("Enter a valid label value.")).toBeNull();
  fireEvent.change(value, { target: { value: "😀".repeat(257) } });
  expect(screen.getByText("Enter a valid label value.")).toBeTruthy();
});

it("rejects creation overrides whose merged preview exceeds 32 labels", async () => {
  const valid = vi.fn();
  render(
    <LabelOverridesField
      inherited={Object.fromEntries(
        Array.from({ length: 32 }, (_, index) => [`parent-${index}`, "yes"]),
      )}
      value={{ extra: "no" }}
      onChange={vi.fn()}
      onValidityChange={valid}
    />,
  );
  expect(
    screen.getByText("Labels may contain at most 32 entries."),
  ).toBeTruthy();
  await waitFor(() => expect(valid).toHaveBeenLastCalledWith(false));
});

it("retains a 412 draft and its original ETag across a background refetch", async () => {
  const read = vi
    .fn()
    .mockResolvedValueOnce({
      value: { labels: { team: "platform" } },
      etag: '"old"',
    })
    .mockResolvedValueOnce({
      value: { labels: { team: "service" } },
      etag: '"new"',
    });
  const write = vi.fn().mockRejectedValue(new Error("This resource changed"));
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={cache}>
      <ResourceLabelsDialog
        resourceId="agent_one"
        labels={{ team: "platform" }}
        editable
        read={read}
        write={write}
      />
    </QueryClientProvider>,
  );

  await user.click(screen.getByRole("button", { name: /Labels/ }));
  const value = await screen.findByLabelText("Label value");
  await user.clear(value);
  await user.type(value, "runtime");
  cache.setQueryData(["resource-labels", "agent_one"], {
    value: { labels: { team: "background" } },
    etag: '"background"',
  });
  expect((screen.getByLabelText("Label value") as HTMLInputElement).value).toBe(
    "runtime",
  );
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  await screen.findByText("This resource changed");
  expect(write).toHaveBeenCalledWith({ team: "runtime" }, '"old"');
  expect((screen.getByLabelText("Label value") as HTMLInputElement).value).toBe(
    "runtime",
  );

  await user.click(screen.getByRole("button", { name: "Refresh labels" }));
  await waitFor(() =>
    expect(
      (screen.getByLabelText("Label value") as HTMLInputElement).value,
    ).toBe("service"),
  );
});
