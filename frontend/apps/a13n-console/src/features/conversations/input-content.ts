import type { Schema } from "../../shared/api";

type Block = Schema["MessagePayload"]["content"][number];

/** Keep the canonical typed block visible without rendering native prepared input. */
export function inputContent(block: Block): string {
  switch (block.type) {
    case "text":
      return block.text;
    case "json":
      return JSON.stringify(block.value);
    case "asset":
      return block.asset_id;
    case "url":
      return block.url;
    case "environment_path":
      return `${block.mount}:${block.path}`;
  }
}
