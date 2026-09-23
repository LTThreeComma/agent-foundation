export const managedFlowKey = "a13n:managed-enrollment";
export type ManagedSelector = {
  workspaceId: string;
  connectionId: string;
  authorizationId: string;
  generation: number;
};
export function saveManagedSelector(value: ManagedSelector) {
  sessionStorage.setItem(managedFlowKey, JSON.stringify(value));
}
export function takeManagedSelector(): ManagedSelector | null {
  const raw = sessionStorage.getItem(managedFlowKey);
  sessionStorage.removeItem(managedFlowKey);
  try {
    const value = JSON.parse(raw ?? "null");
    return value &&
      typeof value.workspaceId === "string" &&
      typeof value.connectionId === "string" &&
      typeof value.authorizationId === "string" &&
      Number.isSafeInteger(value.generation) &&
      value.generation > 0
      ? value
      : null;
  } catch {
    return null;
  }
}
