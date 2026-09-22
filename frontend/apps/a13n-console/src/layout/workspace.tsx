import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { useAuth } from "../auth/context";
import { data, type components } from "../service-client";
import { ErrorPage, Loading } from "../shared/feedback";

type Workspace = components["schemas"]["Workspace"];
const Context = createContext<Workspace | null>(null);
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { workspaceRef = "" } = useParams();
  const { client, user } = useAuth();
  const query = useQuery({
    queryKey: [user?.id, "workspace", workspaceRef],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces/{workspace_id}", {
          params: { path: { workspace_id: workspaceRef } },
          signal,
        }),
      ),
  });
  useEffect(() => {
    if (query.data && user) {
      try {
        localStorage.setItem(`a13n-workspace:${user.id}`, query.data.id);
      } catch {
        /* This visit still works without storage. */
      }
    }
  }, [query.data, user]);
  if (query.error)
    return (
      <ErrorPage
        error={query.error}
        actions={<Link to="/">{t("Choose a workspace")}</Link>}
      />
    );
  if (!query.data) return <Loading />;
  return (
    <Context value={query.data}>
      <div key={`${user?.id}:${query.data.id}`}>{children}</div>
    </Context>
  );
}
export function useWorkspace() {
  const workspace = useContext(Context);
  if (!workspace) throw new Error("WorkspaceProvider is required");
  return workspace;
}
export function useScope() {
  const { client, user } = useAuth();
  const workspace = useWorkspace();
  return {
    client,
    workspace,
    path: { workspace_id: workspace.id },
    cache: [user?.id, workspace.id],
    base: `/workspace/${workspace.id}`,
  };
}
