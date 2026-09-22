"""Export the Native HTTP and existing streaming wire models without opening process resources."""

import argparse
import json
from pathlib import Path

from a13n_service.app import build_app
from a13n_service.infra.cursors import encode
from a13n_service.runs.delivery import ControlPayload, DataPayload, frame
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "proto/a13n-service"


def documents() -> dict[str, dict]:
    cursor = encode("run-stream", "run_example", 1, 1)
    data = DataPayload(
        attempt_number=1,
        event_sequence=1,
        event={"type": "TEXT_MESSAGE_START", "messageId": "msg_example", "role": "assistant"},
    )
    control = ControlPayload(display_version="display_example", cursor=cursor, retry_after_ms=0)
    return {
        "openapi.json": build_app().openapi(),
        "run-stream.schema.json": TypeAdapter(DataPayload | ControlPayload).json_schema(),
        "run-stream.examples.json": {
            "data": frame("data", data.model_dump(mode="json"), cursor=cursor),
            "reset": frame("reset", control.model_dump(mode="json")),
            "closed": frame("closed", control.model_dump(mode="json")),
            "retry_later": frame(
                "retry_later", control.model_copy(update={"retry_after_ms": 1000}).model_dump(mode="json")
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for name, schema in documents().items():
        target = TARGET / name
        if args.check:
            try:
                current = json.loads(target.read_text())
            except (OSError, ValueError):
                current = None
            if current != schema:
                changed.append(name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    if changed:
        parser.exit(1, f"Service contract changed: {', '.join(changed)}. Run make service-contract-generate.\n")


if __name__ == "__main__":
    main()
