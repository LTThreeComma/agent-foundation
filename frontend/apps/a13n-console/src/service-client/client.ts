import createFetchClient from "openapi-fetch";
import type { paths } from "./schema.js";
import { Transport, type ClientOptions } from "./transport.js";

export function createClient(options: ClientOptions) {
  const transport = new Transport(options);
  return {
    http: createFetchClient<paths>(transport.httpOptions()),
    fetch: transport.fetch,
    setCsrfToken: (token: string | undefined) => transport.setCsrfToken(token),
    close: () => transport.close(),
  };
}
export type Client = ReturnType<typeof createClient>;
