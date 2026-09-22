import { useState } from "react";
import { Button, FormField, Input, Logo } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../shared/feedback";
import { useAuth } from "./context";
import styles from "./auth.module.css";

export function LoginPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  return (
    <main className={styles.page}>
      <form
        className={styles.form}
        onSubmit={async (event) => {
          event.preventDefault();
          setPending(true);
          setError(null);
          try {
            await auth.login(email, password);
          } catch (failure) {
            setError(failure);
          } finally {
            setPassword("");
            setPending(false);
          }
        }}
      >
        <Logo alt="Agent Foundation" className="size-10" />
        <h1>{t("Welcome to Agent Foundation")}</h1>
        <p>{t("Sign in to your workspace.")}</p>
        <FormField label={t("Email")}>
          <Input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </FormField>
        <FormField label={t("Password")}>
          <Input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </FormField>
        <ErrorNotice error={error ?? auth.error} />
        <Button type="submit" loading={pending} disabled={pending}>
          {t("Sign in")}
        </Button>
      </form>
    </main>
  );
}
