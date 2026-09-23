import { useEffect, useRef, useState } from "react";
import { Button, Logo } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../auth/context";
import { data } from "../../service-client";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { takeManagedSelector } from "./managed-flow";
import styles from "../../auth/auth.module.css";

declare global {
  interface Window {
    __a13nManagedCallback?: { sessionUri: string | null; invalid: boolean };
  }
}
// The document removes the sensitive query before any application module executes.
let callback = window.__a13nManagedCallback;
delete window.__a13nManagedCallback;

export function ManagedVerifier() {
  const auth = useAuth();
  const { t } = useTranslation();
  const started = useRef(false);
  const [confirm, setConfirm] = useState<(() => Promise<void>) | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (auth.pending || started.current) return;
    started.current = true;
    const received = callback;
    callback = undefined;
    const selector = takeManagedSelector();
    if (!auth.user || !selector || !received || received.invalid) {
      setError(
        new Error(
          "This connection attempt cannot be verified. Return to Connections and reconnect your account.",
        ),
      );
      return;
    }
    const complete = async () => {
      try {
        const completed = data(
          await auth.client.http.POST(
            "/api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorization/complete",
            {
              params: {
                path: {
                  workspace_id: selector.workspaceId,
                  connection_id: selector.connectionId,
                },
              },
              body: {
                authorization_id: selector.authorizationId,
                generation: selector.generation,
                session_uri: received.sessionUri,
              },
            },
          ),
        );
        window.location.replace(completed.return_url);
      } catch (failure) {
        setError(failure);
      } finally {
        received.sessionUri = null;
      }
    };
    if (received.sessionUri === null) setConfirm(() => complete);
    else void complete();
  }, [auth.pending, auth.user, auth.client]);
  return (
    <main className={styles.page}>
      <section className={styles.form}>
        <Logo alt="Agent Foundation" className="size-10" />
        <h1>
          {t(
            error
              ? "Reconnect your account"
              : confirm
                ? "Confirm your account"
                : "Connecting your account",
          )}
        </h1>
        {error ? (
          <>
            <ErrorNotice error={error} />
            <p>{t("Return to Connections to start a new authorization.")}</p>
            <Button onClick={() => window.location.replace("/")}>
              {t("Return to workspace")}
            </Button>
          </>
        ) : confirm ? (
          <>
            <p>
              {t(
                "Confirm the account you just connected. This uses the account saved for this enrollment.",
              )}
            </p>
            <Button
              onClick={() => {
                setConfirm(null);
                void confirm();
              }}
            >
              {t("Confirm connected account")}
            </Button>
          </>
        ) : (
          <Loading />
        )}
      </section>
    </main>
  );
}
