# Kubernetes deployment

Kubernetes rollout is unavailable during the Service foundation phase. Chart rendering and the local kind launcher fail explicitly before deployment or store mutation. The previous chart's settings, secrets, role routing and bootstrap invitation flow must be replaced before rollout; they are not current operating instructions.

The new container can migrate, bootstrap and start its foundation roles independently. See [the foundation guide](../../docs/a13n-service/index.md). Do not point it at any old PostgreSQL database, Redis namespace or object store. Deployment switching requires an explicit target and consumer rollout; no deployment is performed by this change.
