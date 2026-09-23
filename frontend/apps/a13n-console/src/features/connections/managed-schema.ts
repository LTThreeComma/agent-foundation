function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Apply the selected auth configuration's native instance fields to its form. */
export function managedSetupSchema(
  schema: Record<string, unknown>,
  authConfig: unknown,
) {
  if (!object(schema.properties) || !Array.isArray(schema.allOf)) return schema;
  for (const entry of schema.allOf) {
    if (!object(entry) || !object(entry.if) || !object(entry.if.properties))
      continue;
    const selection = entry.if.properties.auth_config_id;
    if (
      !object(selection) ||
      !Array.isArray(selection.enum) ||
      !selection.enum.includes(authConfig)
    )
      continue;
    if (!object(entry.then) || !object(entry.then.properties)) continue;
    return {
      ...schema,
      properties: { ...schema.properties, ...entry.then.properties },
    };
  }
  return schema;
}
