# Foundation Web

Foundation Web is the private browser application bundled into the Foundation Service container image. It is not published to npm.

The application uses relative `/api` URLs. During local development, Vite proxies `/api`, `/healthz`, and `/readyz` to Foundation Service at `http://127.0.0.1:8000`. In production, Foundation Service serves the built assets and API from one origin.

Run it through the repository targets:

```bash
make foundation-web-check
make foundation-web-build
make dev
```

`make dev` opens the backend on port 8000 and the browser development server on port 5173. Set `FOUNDATION_API_PROXY_TARGET` only when the local backend uses another origin.
