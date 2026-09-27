export function providersPath(category: string, workspaceKey: string) {
  return `/workspace/${encodeURIComponent(workspaceKey)}/settings/providers?${new URLSearchParams({ category })}`;
}
