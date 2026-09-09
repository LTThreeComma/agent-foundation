"""Real effect and tool barriers, with the production Harness compactor."""

import os
import re
from pathlib import Path

import anyio
from a13n_harness import AbstractHarnessPlugin, AgentContext
from a13n_harness.plugin_factories import HarnessPluginFactory
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: str
    compact: bool = False


async def wait_file(path, ready, release):
    await anyio.Path(path / ready).touch()
    with anyio.fail_after(180):
        while not any((path / name).exists() for name in (release, "release")):
            await anyio.sleep(0.02)


class RecoveryCapability(AbstractCapability[AgentContext]):
    id = "live-recovery"

    def __init__(self, root):
        self.root = Path(root)

    def get_toolset(self):
        def owned(case_id):
            if not re.fullmatch(r"[a-f0-9]{32}", case_id):
                raise ValueError("Invalid recovery case")
            path = self.root / case_id
            if not (path / "case.json").is_file():
                raise ValueError("Unknown recovery case")
            return path

        async def live_recovery_step(case_id: str) -> str:
            """Wait while the test accepts a durable inbox entry."""
            path = owned(case_id)
            await wait_file(path, "inbox_ready", "inbox_release")
            return "recovery-step-complete"

        async def live_recovery_effect(case_id: str, idempotent: bool) -> str:
            """Persist a tool-owned effect, optionally deduplicated by the exact case key."""
            path = owned(case_id)

            def effect():
                with (path / "effect_attempts").open("a") as output:
                    output.write("attempt\n")
                if idempotent:
                    try:
                        fd = os.open(path / "effect_receipt", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    except FileExistsError:
                        return
                    # The receipt file itself is the synthetic external effect.
                    with os.fdopen(fd, "w") as receipt:
                        receipt.write("effect-committed\n")
                        receipt.flush()
                        os.fsync(receipt.fileno())
                else:
                    with (path / "effects").open("a") as output:
                        output.write("effect-committed\n")
                        output.flush()
                        os.fsync(output.fileno())

            await anyio.to_thread.run_sync(effect)
            await wait_file(path, "effect_ready", "effect_release")
            return "effect-confirmed"

        async def live_recovery_handoff_step(case_id: str, step: int) -> str:
            """Reach one complete tool boundary in a two-step drain journey."""
            if step not in {1, 2}:
                raise ValueError("Unknown handoff step")
            await wait_file(owned(case_id), f"handoff_ready_{step}", f"handoff_release_{step}")
            return f"handoff-step-{step}-complete"

        return FunctionToolset(
            [Tool(live_recovery_step), Tool(live_recovery_effect), Tool(live_recovery_handoff_step)],
            id="live-recovery-tools",
        )


class Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id, configuration):
        self._id, self.configuration = plugin_id, configuration

    @property
    def plugin_id(self):
        return self._id

    def get_capabilities(self):
        return [RecoveryCapability(self.configuration.root)]


class Factory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls):
        return "live.recovery"

    def validate_configuration(self, configuration):
        return Configuration.model_validate(dict(configuration))

    def create_plugin(self, context):
        return Plugin(context.plugin_id, Configuration.model_validate(dict(context.configuration)))
