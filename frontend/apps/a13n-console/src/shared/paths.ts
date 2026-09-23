export const resourceKeyPattern = "[a-z0-9]+(-[a-z0-9]+)*";
/** Any key the Service accepts; keys the Console creates follow the narrower `resourceKeyPattern`. */
export function isResourceKey(value: string): boolean {
  return /^[a-z0-9][a-z0-9_-]{0,127}$/.test(value);
}

export function workspacePath(workspace: { key: string }): string {
  return `/workspace/${workspace.key}`;
}
