# Single-host Service with native Docker

`a13n-service-single-host.yaml` runs the Service with every role in one process (`run --role all`), PostgreSQL, Redis and the Console. Only the Console is published, on `127.0.0.1:8080`. It serves the browser application and proxies `/api` and `/readyz` to the Service, so browsers, API clients and SDKs share one origin. The Service migrates its schema when it starts. The non-root Service process uses the host Docker Engine through `/var/run/docker.sock`. Access to this socket grants host Docker authority, so deploy only where the Service is trusted with that authority.

Build the images, then create the Compose inputs once from the repository root: a credential encryption key, and the socket's numeric group ID **as mounted inside a container**, which can differ from the host value on Docker Desktop:

```sh
make image-a13n-service image-a13n-console image-docker-environment
umask 077
(set -C; python3 -c 'import base64,secrets; print("A13N_ENCRYPTION_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode())' > .env.single-host)
echo "A13N_DOCKER_SOCKET_GID=$(docker run --rm --entrypoint stat -v /var/run/docker.sock:/var/run/docker.sock a13n-service:local -c %g /var/run/docker.sock)" >> .env.single-host
docker compose --env-file .env.single-host -f deploy/compose/a13n-service-single-host.yaml up -d --wait
```

Keep `.env.single-host`: its key encrypts stored provider and connection credentials, which become unreadable without it. `A13N_SERVICE_IMAGE` and `A13N_CONSOLE_IMAGE` select other image tags; the defaults are the local builds above.

Create the first administrator. The command prompts for a password of at least 12 characters and refuses once the Service is initialized:

```sh
docker compose --env-file .env.single-host -f deploy/compose/a13n-service-single-host.yaml exec service \
  a13n-service --config /app/service.toml bootstrap --email admin@example.com
```

For unattended setup, pipe the password twice to `exec -T` instead; the command warns that it cannot hide input without a terminal. Then open <http://127.0.0.1:8080> and sign in. Use exactly this origin: the Service accepts browser state changes only from its public URL, so `http://localhost:8080` cannot sign in. `A13N_CONSOLE_PORT` publishes another port and moves the public URL with it. <http://127.0.0.1:8080/readyz> reports Service readiness and `/healthz` the Console itself.

## Configuration

The mounted `a13n-service-single-host.toml` is the shared configuration. The Compose file adds the public URL and the encryption key ring as `A13N_<SECTION>__<FIELD>` variables, which override the file. See the [configuration reference](../../docs/a13n-service/configuration-reference.md) for every setting. The included database password protects only this unpublished local database. The Service reads its configuration at startup: after an edit, run the Compose command with `restart service`, and the Console restarts with it.

Outbound provider requests reject private addresses and plain HTTP by default. To use, for example, a model server on the Docker host, allow it explicitly:

```toml
[providers]
private_domains = ["host.docker.internal"]
http_origins = ["http://host.docker.internal:11434"]
```

## Docker environments

Add an environment provider of type `docker` in Console or through `POST /api/v1/organizations/{organization_id}/environment-providers`; its default Engine address is the mounted socket. Environment templates choose the image in their recipe. The default, `ghcr.io/converge-ai-labs/a13n-docker-environment:dev`, is published from `main`, and `make image-docker-environment` builds `a13n-docker-environment:local`. A locally built image is immediately available to templates using the same host Engine, and missing images are pulled when first needed. Pin a digest for reproducibility. Rebuilding or pulling a tag does not recreate existing environments; delete an environment and create another to use a new image version.

Docker templates have a private `/workspace` and may bind explicitly approved existing host directories. Mount sources resolve in the host Engine filesystem namespace and need permissions suitable for the container user. Environment deletion preserves these external paths. The hosted sandbox providers and external envd targets are also available; the development-only `local` provider is not offered.

Environment containers run on the host Engine outside this Compose project and carry the label `a13n.environment=<environment ID>`. Unmount and delete environments through Console or the API before removing the stack.

## Data

`postgres`, `redis` and `service-data` (Service objects under `/app/var/objects`) persist across `stop`/`up` and container recreation. `down` retains them; `down -v` destroys the deployment's data. Back up PostgreSQL, the `service-data` volume and `.env.single-host` together.
