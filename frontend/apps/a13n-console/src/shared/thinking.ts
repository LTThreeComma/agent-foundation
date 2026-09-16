import type { Schema } from "./api";

export const THINKING_EFFORTS = [
  "minimal",
  "low",
  "medium",
  "high",
  "xhigh",
] as const;
export type ThinkingEffort = (typeof THINKING_EFFORTS)[number];

export function effortLabel(
  t: (key: string) => string,
  effort: string,
): string {
  return t(
    (
      {
        minimal: "Minimal",
        low: "Low",
        medium: "Medium",
        high: "High",
        xhigh: "Extra high",
      } as Record<string, string>
    )[effort] ?? effort,
  );
}

/** Encodes a native `thinking` settings value for the shared effort select. */
export function encodeThinking(thinking: Schema["JsonValue"] | undefined) {
  if (thinking === undefined) return "unset";
  if (thinking === false) return "off";
  if (thinking === true) return "on";
  return String(thinking);
}
export function withThinking(
  settings: Record<string, Schema["JsonValue"]>,
  encoded: string,
): Record<string, Schema["JsonValue"]> {
  const next = { ...settings };
  if (encoded === "unset") delete next.thinking;
  else
    next.thinking =
      encoded === "off" ? false : encoded === "on" ? true : encoded;
  return next;
}
