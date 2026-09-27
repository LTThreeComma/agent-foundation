export const resourceKeyPattern = "[a-z0-9]+(-[a-z0-9]+)*";

export function workspacePath(workspace: { key: string }): string {
  return `/workspace/${workspace.key}`;
}
