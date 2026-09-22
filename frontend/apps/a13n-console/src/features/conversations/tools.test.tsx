import { expect, test } from "vitest";
import { toolEvents } from "./tools";

test("folds durable and live tool events by original call identity without inventing success", () => {
  const start = {
    type: "TOOL_CALL_START",
    toolCallId: "call-one",
    toolCallName: "increment",
  };
  const pending = toolEvents([
    start,
    { type: "TOOL_CALL_ARGS", toolCallId: "call-one", delta: '{"amount":' },
    { type: "TOOL_CALL_ARGS", toolCallId: "call-one", delta: "1}" },
    { type: "TOOL_CALL_END", toolCallId: "call-one" },
  ]);
  expect(pending).toEqual([
    { id: "call-one", name: "increment", arguments: '{"amount":1}' },
  ]);
  const completed = toolEvents([
    start,
    {
      type: "TOOL_CALL_RESULT",
      toolCallId: "call-one",
      content: "Operation may have completed; no recorded result.",
    },
  ]);
  expect(completed[0].result).toContain("may have completed");
  expect(toolEvents([{ ...start, metadata: { display: false } }])).toEqual([]);
});
