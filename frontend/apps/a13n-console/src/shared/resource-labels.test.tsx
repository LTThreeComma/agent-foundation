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
import { afterEach, expect, it, vi } from "vitest";
import { ResourceLabelsPanel } from "./resource-labels";
import { ResourceReference } from "./resource-reference";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

function setup(editable = true) {
  const read = vi.fn().mockResolvedValue({
    value: { labels: { team: "platform" } },
    etag: '"one"',
  });
  const write = vi.fn().mockResolvedValue(undefined);
  const saved = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ResourceReference id="sk_test" resourceKey="test">
        <ResourceLabelsPanel
          resourceId="sk_test"
          labels={{ team: "platform" }}
          editable={editable}
          read={read}
          write={write}
          onSaved={saved}
        />
      </ResourceReference>
    </QueryClientProvider>,
  );
  return { read, write, saved };
}

it("shows labels inside the identity popover and edits only on request", async () => {
  const { write, saved } = setup();
  const user = userEvent.setup();
  expect(screen.queryByText("team=platform")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Show resource reference" }),
  );
  expect(await screen.findByText("team=platform")).toBeDefined();
  expect(screen.queryByLabelText("Label key")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Edit labels" }));
  await screen.findByLabelText("Label key");
  await user.click(screen.getByRole("button", { name: "Add label" }));
  fireEvent.change(screen.getAllByLabelText("Label key")[1], {
    target: { value: "batch" },
  });
  fireEvent.change(screen.getAllByLabelText("Label value")[1], {
    target: { value: "eval" },
  });
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  await waitFor(() =>
    expect(write).toHaveBeenCalledWith(
      { team: "platform", batch: "eval" },
      '"one"',
    ),
  );
  expect(saved).toHaveBeenCalledOnce();
  expect(screen.queryByLabelText("Label key")).toBeNull();
});

it("preserves the draft after a conflict and refreshes only its precondition", async () => {
  const { read, write } = setup();
  write.mockRejectedValueOnce(new Error("Labels changed"));
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Show resource reference" }),
  );
  await user.click(await screen.findByRole("button", { name: "Edit labels" }));
  fireEvent.change(await screen.findByLabelText("Label value"), {
    target: { value: "my-draft" },
  });
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  await screen.findByText("Labels changed");
  read.mockResolvedValue({
    value: { labels: { team: "theirs" } },
    etag: '"two"',
  });
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await waitFor(() => expect(screen.queryByText("Labels changed")).toBeNull());
  expect((screen.getByLabelText("Label value") as HTMLInputElement).value).toBe(
    "my-draft",
  );
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  await waitFor(() =>
    expect(write).toHaveBeenLastCalledWith({ team: "my-draft" }, '"two"'),
  );
});

it("rejects duplicate keys, cancels without writing, and saves an explicit clear", async () => {
  const { write } = setup();
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Show resource reference" }),
  );
  await user.click(await screen.findByRole("button", { name: "Edit labels" }));
  await screen.findByLabelText("Label key");
  await user.click(screen.getByRole("button", { name: "Add label" }));
  fireEvent.change(screen.getAllByLabelText("Label key")[1], {
    target: { value: "team" },
  });
  expect(screen.getByText("Label keys must be unique.")).toBeDefined();
  expect(
    screen
      .getByRole("button", { name: "Save labels" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(write).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Edit labels" }));
  await screen.findByLabelText("Label key");
  await user.click(screen.getByRole("button", { name: "Clear labels" }));
  await user.click(screen.getByRole("button", { name: "Save labels" }));
  await waitFor(() => expect(write).toHaveBeenCalledWith({}, '"one"'));
});

it("offers no edit controls for read-only labels", async () => {
  const { read, write } = setup(false);
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Show resource reference" }),
  );
  expect(await screen.findByText("team=platform")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Edit labels" })).toBeNull();
  expect(read).not.toHaveBeenCalled();
  expect(write).not.toHaveBeenCalled();
});
