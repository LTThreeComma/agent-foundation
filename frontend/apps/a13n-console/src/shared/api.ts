import { ApiError, data, type components } from "../service-client";
export { data };
export type Schema = components["schemas"];
export function representation<T>(result: { data?: T; response: Response }) {
  return {
    value: data(result),
    etag: result.response.headers.get("ETag") ?? undefined,
  };
}
export function isUnauthorized(error: unknown) {
  return error instanceof ApiError && error.status === 401;
}
