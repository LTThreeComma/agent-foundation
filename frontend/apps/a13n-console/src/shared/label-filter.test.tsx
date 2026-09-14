// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router";
import { LabelFilterField } from "./label-filter";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: { value?: string }) =>
      values?.value ? key.replace("{{value}}", values.value) : key,
  }),
}));

afterEach(cleanup);

it("keeps repeated exact label filters in the URL and resets pagination", async () => {
  const user = userEvent.setup();
  function Location() {
    return <output aria-label="Location">{useLocation().search}</output>;
  }
  render(
    <MemoryRouter initialEntries={["/?cursor=next&label=team%3Dplatform"]}>
      <LabelFilterField />
      <Location />
    </MemoryRouter>,
  );

  await user.type(screen.getByLabelText("Label filter"), "stage=prod");
  await user.click(screen.getByRole("button", { name: "Add filter" }));
  const search = screen.getByLabelText("Location").textContent ?? "";
  expect(search).toContain("label=stage%3Dprod");
  expect(search).toContain("label=team%3Dplatform");
  expect(search).not.toContain("cursor");

  await user.click(
    screen.getByRole("button", {
      name: "Remove label filter team=platform",
    }),
  );
  expect(screen.getByLabelText("Location").textContent).not.toContain(
    "team%3Dplatform",
  );
});

it("accepts 256 emoji scalars and rejects surrogate label values", () => {
  render(
    <MemoryRouter>
      <LabelFilterField />
    </MemoryRouter>,
  );
  const input = screen.getByLabelText("Label filter");
  fireEvent.change(input, { target: { value: `note=${"😀".repeat(256)}` } });
  expect(
    screen.getByRole("button", { name: "Add filter" }).hasAttribute("disabled"),
  ).toBe(false);
  fireEvent.change(input, { target: { value: "note=\ud800" } });
  expect(
    screen.getByRole("button", { name: "Add filter" }).hasAttribute("disabled"),
  ).toBe(true);
});
