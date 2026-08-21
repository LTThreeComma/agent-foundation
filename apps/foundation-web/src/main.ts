import "./style.css";

interface OpenApiDocument {
  info?: {
    title?: string;
    version?: string;
  };
}

const root = document.querySelector<HTMLElement>("#app");

if (root === null) {
  throw new Error("Foundation Web root element is missing");
}

root.innerHTML = `
  <section class="shell" aria-labelledby="page-title">
    <p class="eyebrow">Agent Foundation</p>
    <h1 id="page-title">Foundation Service is running.</h1>
    <p class="summary">
      This private web application and the control-plane API ship from one
      service image and one origin.
    </p>
    <dl class="status-card" aria-live="polite">
      <div>
        <dt>API</dt>
        <dd id="api-status">Connecting to <code>/api</code>...</dd>
      </div>
      <div>
        <dt>Health</dt>
        <dd><a href="/healthz">/healthz</a></dd>
      </div>
      <div>
        <dt>Readiness</dt>
        <dd><a href="/readyz">/readyz</a></dd>
      </div>
      <div>
        <dt>API documentation</dt>
        <dd><a href="/api/docs">/api/docs</a></dd>
      </div>
    </dl>
  </section>
`;

const apiStatus = document.querySelector<HTMLElement>("#api-status");

async function loadApiMetadata(): Promise<void> {
  if (apiStatus === null) {
    return;
  }

  try {
    const response = await fetch("/api/openapi.json", {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`API metadata returned HTTP ${response.status}`);
    }

    const document = (await response.json()) as OpenApiDocument;
    const title = document.info?.title ?? "Foundation Service API";
    const version = document.info?.version ?? "unknown";
    apiStatus.textContent = `${title} ${version}`;
    apiStatus.dataset.state = "ready";
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Unknown error";
    apiStatus.textContent = `Unavailable: ${message}`;
    apiStatus.dataset.state = "error";
  }
}

void loadApiMetadata();
