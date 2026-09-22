import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, test, vi } from "vitest";
import { AuthProvider, useAuth } from "./context";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../service-client", async (original) => ({
  ...(await original<typeof import("../service-client")>()),
  createClient: () => ({ http, setCsrfToken: vi.fn(), close: vi.fn() }),
}));
function Probe() {
  const auth = useAuth();
  const [draft, setDraft] = useState("");
  if (auth.pending) return <p>Loading</p>;
  return (
    <>
      <p>{auth.user?.id ?? "Signed out"}</p>
      <input
        aria-label="Draft"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button onClick={() => void auth.logout()}>Logout</button>
    </>
  );
}
const session = { data: { user: { id: "user" }, csrf_token: "csrf" } };
test("background identity refresh retains drafts on network loss and cannot restore a logged-out identity", async () => {
  http.GET.mockResolvedValueOnce(session);
  http.POST.mockResolvedValue({});
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
  await screen.findByText("user");
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "Unsaved instructions" },
  });
  http.GET.mockRejectedValueOnce(new TypeError("Offline"));
  fireEvent.focus(window);
  await waitFor(() => expect(http.GET).toHaveBeenCalledTimes(2));
  expect(screen.getByText("user")).toBeTruthy();
  expect(screen.getByDisplayValue("Unsaved instructions")).toBeTruthy();
  let finish: (value: typeof session) => void = () => {};
  http.GET.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.focus(window);
  fireEvent.click(screen.getByText("Logout"));
  await screen.findByText("Signed out");
  finish(session);
  await waitFor(() => expect(screen.getByText("Signed out")).toBeTruthy());
});
