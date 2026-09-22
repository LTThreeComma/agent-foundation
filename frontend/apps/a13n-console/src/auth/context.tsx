import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  ApiError,
  createClient,
  data,
  type Client,
  type components,
} from "../service-client";

type User = components["schemas"]["Profile"];
type Identity = {
  client: Client;
  user: User | null;
  pending: boolean;
  error: unknown;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  restore: () => Promise<void>;
};
const Context = createContext<Identity | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [client] = useState(() =>
    createClient({ baseUrl: location.origin, auth: { type: "session" } }),
  );
  const [queries] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 60_000 } },
      }),
  );
  const [user, setUser] = useState<User | null>(null);
  const currentUser = useRef<string | null>(null);
  const observation = useRef(0);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState<unknown>(null);
  async function restore(background = false) {
    const version = ++observation.current;
    if (!background) setPending(true);
    setError(null);
    try {
      const session = data(await client.http.GET("/api/v1/auth/session"));
      if (version !== observation.current) return;
      if (currentUser.current !== session.user.id) queries.clear();
      currentUser.current = session.user.id;
      client.setCsrfToken(session.csrf_token);
      setUser(session.user);
    } catch (failure) {
      if (version !== observation.current) return;
      if (
        background &&
        !(failure instanceof ApiError && failure.status === 401)
      )
        return;
      queries.clear();
      client.setCsrfToken(undefined);
      currentUser.current = null;
      setUser(null);
      if (!(failure instanceof ApiError && failure.status === 401))
        setError(failure);
    } finally {
      if (version === observation.current) setPending(false);
    }
  }
  useEffect(() => {
    void restore();
    const onFocus = () => {
      void restore(true);
    };
    window.addEventListener("focus", onFocus);
    return () => {
      observation.current += 1;
      window.removeEventListener("focus", onFocus);
      queries.clear();
      client.close();
    };
  }, [client, queries]);
  async function login(email: string, password: string) {
    await client.http.POST("/api/v1/auth/login", { body: { email, password } });
    queries.clear();
    await restore();
  }
  async function logout() {
    await client.http.POST("/api/v1/auth/logout");
    observation.current += 1;
    await queries.cancelQueries();
    queries.clear();
    client.setCsrfToken(undefined);
    currentUser.current = null;
    setUser(null);
  }
  return (
    <Context value={{ client, user, pending, error, login, logout, restore }}>
      <QueryClientProvider client={queries}>{children}</QueryClientProvider>
    </Context>
  );
}
export function useAuth() {
  const context = useContext(Context);
  if (!context) throw new Error("AuthProvider is required");
  return context;
}
