"""Run the production worker with a single object-I/O crash cut, only for process tests."""

import asyncio
import json
import sys
from pathlib import Path

from a13n_service.app import build_app
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.runs import attempts, inputs
from a13n_service.settings import Settings


async def main():
    config_path, cut, signal = sys.argv[1:]
    settings = Settings.model_validate_json(Path(config_path).read_text())
    original = LocalObjects.replace_snapshot

    async def replace(self, key, content, **kwargs):
        result = await original(self, key, content, **kwargs)
        if cut == "before_offer" and key.endswith("/state.json"):
            checkpoint = json.loads(content)
            if checkpoint["sequence"] == 1 and not checkpoint["receipts"]:
                Path(signal).write_text("baseline durable, native offer not reached")
                await asyncio.Event().wait()
        if (
            cut == "waiting_candidate"
            and key.endswith("/state.json")
            and json.loads(content).get("candidate") == "waiting"
        ):
            Path(signal).write_text("waiting candidate durable, seal not reached")
            await asyncio.Event().wait()
        return result

    original_confirm = inputs.confirm

    async def confirm(storage, claim, checkpoint):
        await original_confirm(storage, claim, checkpoint)
        if cut == "feedback_confirmed" and checkpoint.receipts and checkpoint.candidate is None:
            Path(signal).write_text("feedback checkpoint and receipt confirmed before dispatch")
            await asyncio.Event().wait()

    inputs.confirm = confirm
    original_start = attempts.start

    async def start(*args, **kwargs):
        await original_start(*args, **kwargs)
        if cut == "offered":
            Path(signal).write_text("native run started, no input checkpoint durable")
            await asyncio.Event().wait()

    attempts.start = start
    LocalObjects.replace_snapshot = replace
    app = build_app(role="worker", settings=settings)
    async with app.router.lifespan_context(app):
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
