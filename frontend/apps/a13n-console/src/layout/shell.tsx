import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router";
import {
  Button,
  ChoiceField,
  Logo,
  Sheet,
  SheetPopup,
  SheetTitle,
  SheetTrigger,
} from "a13n-ui";
import { ListIcon, SignOutIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/context";
import { ApiError, data } from "../service-client";
import { Page } from "../shared/page";
import { ErrorNotice, ErrorPage, Loading } from "../shared/feedback";
import { Pagination, useCursor } from "../shared/collection";
import { useAppearance } from "./appearance";
import { useScope } from "./workspace";
import { ConnectionsPage } from "../features/connections/page";
import { AgentsPage, AgentPage } from "../features/agents/pages";
import { ModelsPage } from "../features/models/page";
import {
  SessionsPage,
  SessionPage,
  ConversationPage,
} from "../features/conversations/pages";
import styles from "./shell.module.css";

function useWorkspaces() {
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
    return <Navigate to={`/workspace/${previous.data.id}/sessions`} replace />;
  if (query.error) return <ErrorPage error={query.error} />;
  if (!query.data) return <Loading />;
  const first = query.data.items[0];
  if (first) return <Navigate to={`/workspace/${first.id}/sessions`} replace />;
  return (
    <Page
      title={t("No workspaces")}
      description={t("Ask your administrator for workspace access.")}
    >
      <Pagination page={page} next={query.data.next_cursor} />
    </Page>
  );
}
function Navigation({ close }: { close?: () => void }) {
  const { base, workspace } = useScope();
  const { query, page } = useWorkspaces();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useAppearance();
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const options =
    query.data?.items.map((item) => ({ value: item.id, label: item.name })) ??
    [];
  if (!options.some((item) => item.value === workspace.id))
    options.unshift({ value: workspace.id, label: workspace.name });
  return (
    <div className={styles.navigation}>
      <Link to="/" className={styles.brand}>
        <Logo alt="Agent Foundation" className="size-7" />
        <span>Agent Foundation</span>
      </Link>
      <ChoiceField
        label={t("Workspace")}
        value={workspace.id}
        options={options}
        onValueChange={(id) => {
          navigate(`/workspace/${id}/sessions`);
          close?.();
        }}
      />
      <Pagination page={page} next={query.data?.next_cursor} />
      <ErrorNotice error={query.error} />
      <p className={styles.permission}>
        {workspace.permissions.includes("write")
          ? t("Build and run agents")
          : workspace.permissions.includes("run")
            ? t("Run agents")
            : t("View only")}
      </p>
      <nav aria-label={t("Workspace navigation")}>
        {[
          ["sessions", "Sessions"],
          ["agents", "Agents"],
          ["connections", "Connections"],
          ["models", "Models"],
        ].map(([path, label]) => (
          <NavLink
            key={path}
            to={`${base}/${path}`}
            className={({ isActive }) =>
              isActive ? styles.selected : undefined
            }
            onClick={close}
          >
            {t(label)}
          </NavLink>
        ))}
      </nav>
      <div className={styles.account}>
        <ChoiceField
          label={t("Appearance")}
          value={theme}
          options={[
            { value: "system", label: t("System") },
            { value: "light", label: t("Light") },
            { value: "dark", label: t("Dark") },
          ]}
          onValueChange={(value) => {
            if (value === "system" || value === "light" || value === "dark")
              setTheme(value);
          }}
        />
        <ChoiceField
          label={t("Language")}
          value={i18n.language}
          options={[
            { value: "en", label: "English" },
            { value: "zh-CN", label: "简体中文" },
          ]}
          onValueChange={(value) => {
            void i18n.changeLanguage(value);
          }}
        />
        <p>{user?.name || user?.email}</p>
        <Button
          variant="ghost"
          onClick={async () => {
            try {
              await logout();
            } catch (failure) {
              setError(failure);
            }
          }}
        >
          <SignOutIcon />
          {t("Sign out")}
        </Button>
        <ErrorNotice error={error} />
      </div>
    </div>
  );
}
export function Shell() {
  const [open, setOpen] = useState(false);
  const { workspace } = useScope();
  const { t } = useTranslation();
  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <Navigation />
      </aside>
      <div className={styles.mobile}>
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger
            render={
              <Button variant="ghost" aria-label={t("Open navigation")} />
            }
          >
            <ListIcon />
          </SheetTrigger>
          <SheetPopup side="left" className="w-72">
            <SheetTitle className="sr-only">{t("Navigation")}</SheetTitle>
            <Navigation close={() => setOpen(false)} />
          </SheetPopup>
        </Sheet>
        <span>{workspace.name}</span>
      </div>
      <main className={styles.main}>
        <Routes>
          <Route index element={<Navigate to="sessions" replace />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="connections" element={<ConnectionsPage />} />
          <Route path="agents/new" element={<AgentPage />} />
          <Route path="agents/:agentRef" element={<AgentPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="sessions/new" element={<ConversationPage />} />
          <Route path="sessions/:sessionId" element={<SessionPage />} />
          <Route path="threads/:threadId" element={<ConversationPage />} />
          <Route
            path="*"
            element={
              <Page title={t("Page not found")}>
                <Link to="/">{t("Choose a workspace")}</Link>
              </Page>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
