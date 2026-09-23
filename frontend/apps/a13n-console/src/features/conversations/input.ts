import { isObject } from "./projection";

export function inputText(input: unknown, fallback?: string | null): string {
  if (!isObject(input) || !Array.isArray(input.content)) return fallback ?? "";
  return (
    input.content
      .flatMap((block) =>
        isObject(block) &&
        block.type === "text" &&
        typeof block.text === "string"
          ? [block.text]
          : [],
      )
      .join("\n\n") ||
    fallback ||
    ""
  );
}
