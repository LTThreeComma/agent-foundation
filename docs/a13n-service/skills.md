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
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: release-notes-1" -F file=@release-notes.zip

curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/skills" \
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

An agent revision selects skills in `skills` as `{"skill_id": ..., "revision_id": ...}`; without `revision_id`, saving pins the skill's current default revision.

## Secrets

A secret is a value a tool needs at execution, such as a token for a plugin tool. Values are write-only: they are encrypted with the deployment's key ring and never returned.

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/secrets" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "JIRA_TOKEN", "value": "...", "scope": "workspace"}'
```

| Scope       | Owner         | Who can see and change it                                                                                                             |
| ----------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `workspace` | The workspace | Members read its metadata; `write` creates, replaces and deletes it.                                                                  |
| `user`      | The creator   | Only the creator sees and changes it (`run` is enough). A workspace administrator can delete it by ID, but cannot list or replace it. |

One workspace secret and one private secret per person may exist for each key. `PUT …/secrets/{secret_id}` with `{value}` and `If-Match` replaces the value; key and scope never change. `DELETE` removes it.

An agent revision declares the secrets its tools need in `secret_requirements`, such as `[{"key": "JIRA_TOKEN", "scope": "workspace"}]`. A `user` requirement resolves to the run principal's own private secret, so each person's runs use their own value.

- Before a run starts, every declared secret must exist; a missing one fails the run and names only its key.
- A tool receives a secret only if its agent declares every secret the tool asks for, and only for the duration of that call. Inline subagents use their own declarations, not their parent's.
- Runs keep no copy of secret values: a deleted or replaced secret affects later executions.

Secrets are not injected into environments or connection requests; connections and providers keep their own write-only credentials.
