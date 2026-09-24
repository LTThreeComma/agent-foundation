import createFetchClient from "openapi-fetch";
import type { paths } from "./schema.js";
import { Transport, type ClientOptions } from "./transport.js";
import {
  threadStream,
  type ThreadStreamOptions,
} from "./streams/thread-stream.js";

/** One typed HTTP boundary; resource operations stay defined by Service OpenAPI. */
export function createClient(options: ClientOptions) {
  const transport = new Transport(options);
  return {
    http: createFetchClient<paths>(transport.httpOptions()),
    setCsrfToken: (token: string | undefined) => transport.setCsrfToken(token),
    streamThread: (
      workspaceId: string,
      threadId: string,
      streamOptions?: ThreadStreamOptions,
    ) => threadStream(transport, workspaceId, threadId, streamOptions),
    close: () => transport.close(),
  };
}
export type Client = ReturnType<typeof createClient>;
