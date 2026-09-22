import assert from "node:assert/strict";
import { test } from "vitest";
import { ApiError, createClient, ProtocolError } from "./index.js";
import { decodeSse } from "./streams/sse.js";

const baseUrl = "https://service.example";
const json = (value) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });

test("session mutations require CSRF and preserve null, omission and concurrency headers", async () => {
  const requests = [];
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      return json({});
    },
  });
  await assert.rejects(
    client.http.POST("/api/v1/users/me/keys", {
      body: { name: "A", workspace_id: "ws_test" },
    }),
    /CSRF/,
  );
  client.setCsrfToken("csrf-proof");
  await client.http.POST("/api/v1/users/me/keys", {
    headers: { "If-Match": '"version"' },
    body: { name: "A" },
  });
  assert.equal(requests[0].headers.get("X-CSRF-Token"), "csrf-proof");
  assert.equal(requests[0].headers.get("If-Match"), '"version"');
  assert.equal(requests[0].credentials, "same-origin");
  assert.deepEqual(await requests[0].json(), { name: "A" });
  client.close();
  await assert.rejects(client.http.GET("/api/v1/users/me"), {
    name: "AbortError",
  });
});

test("credentials cannot escape through a per-call base URL", async () => {
  let calls = 0;
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "private" },
    fetch: async () => {
      calls++;
      return json({});
    },
  });
  await assert.rejects(
    client.http.GET("/api/v1/users/me", {
      baseUrl: "https://elsewhere.example",
    }),
    /configured Service/,
  );
  assert.equal(calls, 0);
});

test("safe reads retry but unknown mutation outcomes are never replayed", async () => {
  let calls = 0;
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "private" },
    fetch: async () =>
      ++calls === 1
        ? new Response(null, { status: 503, headers: { "Retry-After": "0" } })
        : json({}),
  });
  await client.http.GET("/api/v1/users/me");
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(
    client.http.POST("/api/v1/auth/login", {
      body: { email: "a@example.com", password: "password" },
    }),
    ApiError,
  );
  assert.equal(calls, 1);
});

function chunks(text) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  });
}

test("SSE handles split UTF-8, CRLF, multiline payloads and cancellation", async () => {
  const frames = [];
  for await (const frame of decodeSse(
    chunks(
      ": heartbeat\r\nid: 2-0\r\nevent: message\r\ndata: 你好\r\ndata: world\r\n\r\n",
    ),
  ))
    frames.push(frame);
  assert.deepEqual(frames, [
    { id: "2-0", event: "message", data: "你好\nworld" },
  ]);
  await assert.rejects(async () => {
    for await (const _ of decodeSse(chunks("data: partial"))) {
    }
  }, ProtocolError);
  let canceled = false;
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("data: one\n\n"));
    },
    cancel() {
      canceled = true;
    },
  });
  for await (const _ of decodeSse(body)) break;
  assert.equal(canceled, true);
});
