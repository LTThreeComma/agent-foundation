import { expect, test } from "vitest";
import { encodeThinking, withThinking } from "./thinking";

test("encodeThinking distinguishes unset, off, on, and effort values", () => {
  expect(encodeThinking(undefined)).toBe("unset");
  expect(encodeThinking(false)).toBe("off");
  expect(encodeThinking(true)).toBe("on");
  expect(encodeThinking("high")).toBe("high");
  expect(encodeThinking(3)).toBe("3");
});

test("withThinking sets and clears the key without touching other settings", () => {
  const settings = { temperature: 0.4, thinking: "low" };
  expect(withThinking(settings, "high")).toEqual({
    temperature: 0.4,
    thinking: "high",
  });
  expect(withThinking(settings, "off")).toEqual({
    temperature: 0.4,
    thinking: false,
  });
  expect(withThinking(settings, "on")).toEqual({
    temperature: 0.4,
    thinking: true,
  });
  expect(withThinking(settings, "unset")).toEqual({ temperature: 0.4 });
  expect(settings.thinking).toBe("low");
});
