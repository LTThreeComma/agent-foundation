# Curated Release Notes

A release may include reviewed, human-written notes at:

```text
.github/release-notes/<component>/<version>.md
```

Supported component keys are `foundation`, `agent-envd`, `sdk-python`, `sdk-go`, `sdk-rust`, and `sdk-typescript`. Versions use canonical `X.Y.Z` syntax.

The file is optional. For a channel with an earlier release, its content is prepended to GitHub's generated pull-request notes and channel-scoped Full Changelog. For the first release in a channel, the curated content replaces the default initial-release sentence; no cross-channel generated notes are added. A missing or empty file is treated as no curated content, so the release remains fully automatic.

Use this structure when the sections are relevant:

```markdown
## Highlights

- Describe the most important user-visible changes.

## Upgrade notes

Describe required actions, or state that none are required.

## Known issues

- Describe material limitations that users should understand before upgrading.
```

Do not repeat the release title, generated pull-request list, contributors, or Full Changelog link.
