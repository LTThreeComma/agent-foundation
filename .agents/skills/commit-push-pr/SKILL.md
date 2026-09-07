---
name: commit-push-pr
description: Use when the user requests commits, branch pushes, or GitHub pull request creation or updates.
---

# Commit, Push, and Pull Request

Complete the requested Git/GitHub handoff under `AGENTS.md` and `CONTRIBUTING.md`. Preserve existing authorization across follow-ups. Do not add Issue creation, reviewer requests, PR merges, releases, or deployments unless authorized. Do not start a separate code review or review subagent unless the user requests it.

## Inspect and Prepare

- Inspect `git status --short --branch`, the diff, untracked files, and relevant remotes. Separate intended changes from unrelated work, secrets, local configuration, caches, and generated artifacts.
- Resolve the requested stages from context: commit-only ends after committing; push-only may use an existing commit; opening a PR normally includes the necessary branch, commit, and push. Ask only when the intended content or destination is materially ambiguous.
- Keep a suitable branch. For detached HEAD, a default branch, or another protected branch, follow the user's naming convention when provided; otherwise create a short descriptive branch such as `feat/harness-capability-runtime` or `fix/session-cancellation`. Do not push directly to a protected base.
- GitHub operations use `gh`; local Git work does not require it. Before a GitHub operation, determine the host/repository from the remote and check `gh auth status --hostname <host>` and `gh repo view --json nameWithOwner,url,defaultBranchRef`.
- If `gh` is unavailable or unauthenticated, finish independent authorized local preparation and report the missing prerequisite and applicable login command. Do not silently substitute browser automation, raw APIs, or another hosting CLI.

Unresolved material design questions follow the contribution workflow; this skill does not settle them or authorize an Issue post.

## Validate the Intended Change

Use [Local Validation](../../../CONTRIBUTING.md#local-validation) to select checks for the intended change and complete applicable component requirements. The Git handoff does not itself require another `make check` or a broader test run.

Reuse successful implementation-stage checks while their relevant inputs remain unchanged. If formatting or fixes alter those inputs, review the diff and rerun the affected checks. Record unavailable checks and failures accurately; do not represent them as passing or bypass hooks with `--no-verify`.

Before staging, run `git diff --check`. Stage explicit intended paths and review both the staged diff and `git diff --cached --stat`.

## Commit

Use an English Conventional Commit subject with lowercase type and required scope:

```text
type(scope): imperative summary
```

Keep the subject concise without a trailing period. Describe material completed behavior or boundary changes in the body when needed; do not pad it with routine staging/formatting steps or require a fixed bullet count.

Do not create empty or duplicate commits, add agent co-author trailers, or invent attribution. If hooks rewrite files, inspect and stage only intended changes before committing again. Rewrite existing history only when that operation is explicitly authorized.

## Push

Verify the destination remote and branch from the request and repository configuration, then push with upstream tracking where needed; for a verified `origin` destination:

```bash
git push -u origin HEAD
```

Do not force-push by default. Use `--force-with-lease` only when the concrete history rewrite is explicitly authorized. After an uncertain push result, inspect remote state before retrying.

When local checkout synchronization is part of the user's workflow, inspect that checkout before changing it. Synchronize only a clean checkout with a verified fast-forward; explain the impact and obtain missing authorization if local work, reset, rebase, or merge would be involved. Do not delete branches or worktrees merely to tidy up.

## Create or Update the PR

Check for an existing open PR for the confirmed head/base so updates do not create duplicates:

```bash
gh pr view --json number,url,state,isDraft,title
```

If no open PR exists, create one with the intended head/base. Preserve an existing PR's draft state; create a ready PR unless the user requests a draft.

Use the scoped Conventional Commit format for the PR title, summarizing the complete final diff. Read the repository PR template, preferring the base-branch version. Preserve required headings and checklist items, remove placeholders such as `Closes #`, link a relevant Issue when one exists, and mark only verified conditions. Human-review items require evidence from the human author.

Explain the problem, resulting behavior, material compatibility implications, and exact validation outcomes. If no template exists, a short summary and validation section suffice. Pass multiline content with `--body-file` using a temporary file outside the repository. Do not claim a missing check passed or omit a known blocker.

Inspect `gh pr checks` after creating or updating the PR. A request to create a PR can end with CI status reported as pending. A request to make the PR ready to merge includes waiting for required checks and fixing failures caused by the change; it does not authorize merging. Report external blockers and unmet review requirements accurately. Before retrying an uncertain PR creation, query existing PRs again.

## Handoff

Report only the completed/requested stages: branch and commit, push result, PR link/title, validation and current CI state, plus concrete blockers. Apply reviewer routing from `MAINTAINERS.md` when requesting reviewers is authorized.
