import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { ToastProvider, TooltipProvider } from "a13n-ui";
import { AuthProvider, useAuth } from "./auth/context";
import { LoginPage } from "./auth/pages";
import { AppearanceProvider } from "./layout/appearance";
import { WorkspaceProvider } from "./layout/workspace";
import { Shell, WorkspaceLanding } from "./layout/shell";
import { Loading } from "./shared/feedback";

function Authenticated() {
  const auth = useAuth();
  if (auth.pending) return <Loading />;
  if (!auth.user) return <LoginPage />;
  return (
    <Routes>
      <Route path="/" element={<WorkspaceLanding key={auth.user.id} />} />
      <Route
        path="/workspace/:workspaceRef/*"
        element={
          <WorkspaceProvider>
            <Shell />
          </WorkspaceProvider>
        }
      />
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
