import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { saveManagedSelector } from "./managed-flow";

const post = vi.hoisted(() => {
  window.__a13nManagedCallback = { sessionUri: null, invalid: false };
  return vi.fn(() => new Promise(() => {}));
});
vi.mock("../../auth/context", () => ({
  useAuth: () => ({
    pending: false,
    user: { id: "current-user" },
    client: { http: { POST: post } },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { ManagedVerifier } from "./managed-verifier";

test("non-OAuth completion requires the signed-in user's explicit confirmation", () => {
  saveManagedSelector({
    workspaceId: "workspace",
    connectionId: "connection",
    authorizationId: "authorization",
    generation: 3,
  });
  render(<ManagedVerifier />);
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm connected account" }),
  );
  expect(post).toHaveBeenCalledExactlyOnceWith(
    "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorization/complete",
    {
      params: {
        path: { workspace_id: "workspace", connection_id: "connection" },
      },
      body: {
        authorization_id: "authorization",
        generation: 3,
        session_uri: null,
      },
    },
  );
  expect(
    screen.queryByRole("button", { name: "Confirm connected account" }),
  ).toBeNull();
});
