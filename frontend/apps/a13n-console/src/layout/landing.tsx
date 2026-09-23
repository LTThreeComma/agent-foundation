import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/context";
import { ApiError, data } from "../service-client";
import { Page } from "../shared/page";
import { ErrorPage, Loading } from "../shared/feedback";
import { Pagination, useCursor } from "../shared/collection";

export function useWorkspaces() {
  const { client, user } = useAuth();
  const page = useCursor();
  const query = useQuery({
    queryKey: [user?.id, "workspaces", page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.http.GET("/api/v1/workspaces", {
          params: { query: { limit: 50, cursor: page.cursor } },
          signal,
        }),
      ),
  });
  return { query, page };
}
export function WorkspaceLanding() {
  const { query, page } = useWorkspaces();
  const { t } = useTranslation();
  const { client, user } = useAuth();
  const [remembered] = useState(() => {
    try {
      return localStorage.getItem(`a13n-workspace:${user?.id}`);
    } catch {
      return null;
    }
  });
  const previous = useQuery({
    queryKey: [user?.id, "remembered-workspace", remembered],
    enabled: !!remembered,
    queryFn: async ({ signal }) => {
      try {
        return data(
          await client.http.GET("/api/v1/workspaces/{workspace_id}", {
            params: { path: { workspace_id: remembered ?? "" } },
            signal,
          }),
        );
      } catch (error) {
        if (error instanceof ApiError && [403, 404].includes(error.status))
          return null;
        throw error;
      }
    },
  });
  if (remembered && previous.isPending) return <Loading />;
  if (previous.error) return <ErrorPage error={previous.error} />;
  if (previous.data)
    return <Navigate to={`/workspace/${previous.data.id}/agents`} replace />;
  if (query.error) return <ErrorPage error={query.error} />;
  if (!query.data) return <Loading />;
  const first = query.data.items[0];
  if (first) return <Navigate to={`/workspace/${first.id}/agents`} replace />;
  return (
    <Page
      title={t("No workspaces")}
      description={t("Ask your administrator for workspace access.")}
    >
      <Pagination page={page} next={query.data.next_cursor} />
    </Page>
  );
}
