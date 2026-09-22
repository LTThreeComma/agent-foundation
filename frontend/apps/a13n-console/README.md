# a13n Console

The private React and TypeScript application for Agent Foundation Service. It uses shared `a13n-ui` components and one generated HTTP boundary. English is the default; Simplified Chinese and light/dark/system appearance are available in navigation.

## Local development

For a complete browser journey with disposable PostgreSQL, Redis, local objects, Control, Worker and a real scripted HTTP model:

```bash
make live-test-console CONSOLE_LIVE_DIR=/tmp/a13n-console-review
```

The directory must not exist. The launcher prints the Console URL, model endpoint and public workspace identifiers, and records process logs and `state.json` there. Sign in as `console@example.com` or `viewer@example.com` with the fixture-only password `console-fixture-password`. All stores belong to the launcher; Ctrl+C stops its processes and removes its containers. The browser uses localhost's secure-context cookie behavior; session cookies retain Secure, HttpOnly and SameSite flags. No certificate warning or browser security override is needed.

Create a provider using the printed model endpoint, choose OpenAI Chat Completions, and use `scripted` as the upstream model ID. The endpoint accepts a fixture-only bearer value or its explicitly configured no-auth mode. Create an Agent, send a message, reload its history and continue. A message containing `[interruptible]` delays the fixture response so the Stop run action can be exercised. See [the reproducible browser journey](../../../dev/live_tests/console-journey.md).

To point Vite at an already running Service:

```bash
make frontend-sync
A13N_CONSOLE_SERVICE_URL=http://127.0.0.1:8000 pnpm --dir frontend --filter a13n-console dev
```

Use `make dev-status` for the ordinary checkout-owned Service port. Vite proxies `/api` without changing its origin and logs method/path/status only. Production ingress must serve Console assets and route `/api` to Service on the same origin. Service does not host browser assets.

## Implemented milestone

- Cookie login, authenticated session/CSRF restoration, logout and scoped workspace selection.
- Workspace Model/Provider creation and bounded selection; write-only credentials.
- Full-page Agent configuration, immutable revision history and default selection with strong ETags.
- Session and Thread navigation, input submission/replay, pending/assigned/consumed inbox states, continuation and exact-run interruption.
- Bounded durable history plus attempt-local text updates. Reset/loss reloads durable state before reconnecting; navigation closes observation without interrupting execution.

Workspace paths accept exact IDs or globally unique keys. New links use authorized IDs. Ambiguous or forbidden deep links fail explicitly; they never select another workspace. Query caches include user and resolved workspace identity. CSRF and credentials are never stored in browser persistence.

The retained broader product contract is [Console](../../../spec/frontend/console.md). Waiting/approval/client-tool flows, fork/children, Connections/MCP, Skills, Environments, broader administration and trace surfaces are not yet implemented. Removed Bot, memory, pairing and configuration-draft integrations are not compatibility inputs.

## Contracts and validation

Run `make service-contract-generate` after changing Service routes or models. It exports OpenAPI and SSE payload schemas/examples and regenerates `src/service-client/schema.ts`. Never edit generated output. `make service-contract-check` detects drift without writes. Independent SDK/remote CLI repositories consume these contracts separately.

```bash
pnpm --dir frontend --filter a13n-console typecheck
pnpm --dir frontend --filter a13n-console test
pnpm --dir frontend --filter a13n-console build
```

Node tests cover transport, framing and recovery; jsdom tests exercise keyboard submission, uncertain retries and permissions. Real browser evidence requires the disposable launcher, not a mocked API or a successful build. The production build is emitted to ignored `dist/`.
