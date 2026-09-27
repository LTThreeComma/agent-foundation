# Skills and secrets

## Skills

A skill is a package of instructions and supporting files that an agent can load when a task calls for it. Skills are [revisioned](resources.md#lifecycles): each change adds an immutable revision, and agent revisions pin the exact skill revision they use. See [Harness skills](../a13n-harness/skills.md) for how agents use them at run time.

### Package format

A skill package is a zip archive with `SKILL.md` at its root or inside its only top-level directory. `SKILL.md` is UTF-8 with YAML front matter that declares at least `name` and `description`:

```markdown
---
name: release-notes
description: Write release notes from merged pull requests.
---

# Release notes

1. List the merged pull requests since the last tag...
```

Limits: at most 1000 files, 8 MiB per file, 32 MiB expanded, 256 KiB for `SKILL.md`, and paths up to 1024 bytes. Members must be regular files with relative paths, stored or deflated, and not encrypted.

### Add a skill

In Console, open **Skills → Import skill** and choose a ZIP file or a GitHub repository. Through the API, a skill's source is either an upload or a public GitHub directory:

```sh
# Stage the archive (see Uploads), then create the skill from it
curl -X POST "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: release-notes-1" -F file=@release-notes.zip

curl -X POST "$A13N_URL/api/v1/skills" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"source": {"kind": "upload", "upload_id": "upl_..."}}'
```

```json
{"source": {"kind": "github", "repository": "owner/repo", "ref": "main", "path": "skills/release-notes"}}
```

- `key`, `name` and `description` default to those in `SKILL.md`; pass them to override. The key must be unique in the workspace.
- GitHub imports read public repositories anonymously. `ref` defaults to the default branch and `path` to the repository root. The resolved commit is recorded; pass `commit` to require a specific one (`409 conflict`, reason `commit_mismatch`, otherwise). Each GitHub request is bounded by `control.import_timeout`.
- Upload size is bounded by `objects.upload_bytes` (1 MiB by default).
- `POST …/skills/validate` with `{"source": ...}` checks a package exactly as creation would and returns its manifest (name, description, files, sizes, digest and resolved source) without storing anything.

### Revisions

- `POST …/skills/{skill_id}/revisions` with `{source, note?, make_default?}` and the skill's `If-Match` adds a revision; it becomes the default unless `make_default` is `false`. A package identical to the current default is a no-op: the call returns that revision again (`201`) without creating one, regardless of `make_default`.
- `GET …/revisions` lists revisions newest first; `POST …/revisions/{revision_id}/set-default` changes the default.
- `GET …/revisions/{revision_id}/content` downloads the archive, and `GET …/revisions/{revision_id}/files/{path}` one file of it.
- `PATCH …/skills/{skill_id}` changes `name`, `description` and `labels`. Lists filter by `label`, `q`, `archived` and `source` (`upload` or `github`, of the default revision).
- `POST …/archive` stops new agent revisions from pinning the skill and refuses changes; agents that already pin it keep working. `POST …/unarchive` reverses it.

An agent revision selects skills in `skills` as `{"id": ..., "revision_id": ...} or {"key": "release-notes", "revision_id": ...}`; without `revision_id`, saving pins the skill's current default revision.

## Secrets

A secret is a value a tool needs at execution, such as a token for a plugin tool. Values are write-only: they are encrypted with the deployment's key ring and never returned.

```sh
curl -X POST "$A13N_URL/api/v1/secrets" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "JIRA_TOKEN", "value": "..."}'
```

All secrets belong to the workspace. `read` reveals metadata only; `write` creates, replaces and deletes values. There is one secret per key in the workspace, with no personal values or scope selector.

`PUT …/secrets/{secret_id}` with `{value}` and `If-Match` replaces the value. The key is immutable. `DELETE` removes the instance and releases its key, but the next instance always has a different ID.

An agent input declares `secret_requirements`, for example `[{"secret": {"key": "JIRA_TOKEN"}}]` or `[{"secret": {"id": "sec_..."}}]`. The Service resolves that selection once and stores its ID and credential audience key.

- Before a run starts, every declared ID must exist. A missing one fails the run naming its ID, without decrypting values.
- A tool receives a value only when its agent declares the requested audience and the call is authorized. Inline subagents use their own declarations.
- Values are read and decrypted per call. Rotation applies to the next call. Deleting a secret makes its old references fail even if a new secret reuses the key.

Secrets are not injected into environments or connection requests; connections and providers keep their own write-only credentials.
