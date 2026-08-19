# Foundation Service SDKs

The Foundation Service SDKs live under one standalone `sdk/` boundary. They are intentionally excluded from the root Python and Rust workspaces so each language can evolve, validate, version, and release independently.

| Language   | Distribution or module                                | Source directory | Release tag                    |
| ---------- | ----------------------------------------------------- | ---------------- | ------------------------------ |
| Python     | `converge-foundation-sdk`                             | `sdk/python`     | `release/sdk/python/X.Y.Z`     |
| Go         | `github.com/converge-ai-labs/agent-foundation/sdk/go` | `sdk/go`         | `release/sdk/go/X.Y.Z`         |
| Rust       | `converge-foundation-sdk`                             | `sdk/rust`       | `release/sdk/rust/X.Y.Z`       |
| TypeScript | `@converge.ai/foundation-sdk`                         | `sdk/typescript` | `release/sdk/typescript/X.Y.Z` |

All four SDKs currently provide publishable `0.0.x` package shells only. They reserve stable package identities without committing the project to a generator, transport, or service contract before the API is ready. SDK language versions are independent.

Run the fast standalone SDK checks while iterating and the complete release checks before publishing:

```bash
make sdk-check
make sdk-check-all
```
