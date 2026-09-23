import { BrowserRouter, Link, Navigate, Route, Routes } from "react-router";
import { ToastProvider, TooltipProvider } from "a13n-ui";
import { AuthProvider, useAuth } from "./auth/context";
import { LoginPage } from "./auth/pages";
import { AppearanceProvider } from "./layout/appearance";
import { WorkspaceProvider } from "./layout/workspace";
import { Shell } from "./layout/shell";
import { WorkspaceLanding } from "./layout/landing";
import { Page } from "./shared/page";
import { useTranslation } from "react-i18next";
import { ConnectionsPage } from "./features/connections/page";
import { AgentsPage, AgentPage } from "./features/agents/pages";
import { ModelsPage } from "./features/models/page";
import { ConversationPage } from "./features/conversations/pages";
import {
  ConversationsPage,
  SessionLayout,
} from "./features/conversations/page";
import { ManagedVerifier } from "./features/connections/managed-verifier";
import { Loading } from "./shared/feedback";

function Authenticated() {
  const { t } = useTranslation();
  const auth = useAuth();
  if (location.pathname === "/managed/verify") return <ManagedVerifier />;
  if (auth.pending) return <Loading />;
  if (!auth.user) return <LoginPage />;
  return (
    <Routes>
      <Route path="/" element={<WorkspaceLanding key={auth.user.id} />} />
      <Route
        path="/workspace/:workspaceRef"
        element={
          <WorkspaceProvider>
            <Shell />
          </WorkspaceProvider>
        }
      >
        <Route index element={<Navigate to="agents" replace />} />
        <Route path="agents" element={<AgentsPage />} />
        <Route path="connections" element={<ConnectionsPage />} />
        <Route path="agents/new" element={<AgentPage />} />
        <Route path="agents/:agentRef" element={<AgentPage />} />
        <Route path="models" element={<ModelsPage />} />
        <Route path="sessions" element={<ConversationsPage />}>
          <Route path="new" element={<ConversationPage />} />
          <Route path=":sessionId" element={<SessionLayout />}>
            <Route path="threads/:threadId" element={<ConversationPage />} />
          </Route>
        </Route>
        <Route path="threads/:threadId" element={<ConversationPage />} />
        <Route
          path="*"
          element={
            <Page title={t("Page not found")}>
              <Link to="/">{t("Choose a workspace")}</Link>
            </Page>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
export function App() {
  return (
    <AppearanceProvider>
      <ToastProvider closeLabel="Close">
        <TooltipProvider>
          <BrowserRouter>
            <AuthProvider>
              <Authenticated />
            </AuthProvider>
          </BrowserRouter>
        </TooltipProvider>
      </ToastProvider>
    </AppearanceProvider>
  );
}
