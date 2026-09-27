/** A model key: lowercase letters, digits, `-` and `.`. */
export const keyPattern = "[a-z0-9][a-z0-9.\\-]{0,127}";

/**
 * The key the Service gives a model created without one: its upstream name
 * after the last `/`, lowercased, when that is a valid key.
 */
export function defaultModelKey(modelName: string): string | undefined {
  const key = modelName.trim().split("/").at(-1)!.toLowerCase();
  return new RegExp(`^${keyPattern}$`).test(key) ? key : undefined;
}
