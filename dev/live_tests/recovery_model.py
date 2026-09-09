"""Deterministic upstream evidence for inbox compaction and uncertain effects."""

import hashlib
import json
import re

import anyio
from a13n_harness.capabilities.context import _COMPACTION_PROMPT
from fastapi.responses import StreamingResponse

from .recovery_plugin import wait_file
from .round_two_model import tool_call

SCENARIOS = {"recovery_inbox", "recovery_child", "recovery_effect", "recovery_handoff"}


def input_values(texts):
    return [value for text in texts for value in re.findall(r"RECOVERY_(?:STEER|CHILD_RESULT) [a-f0-9]{32}", text)]


async def completion(case, path, body, texts, tool_messages):
    from .fixture_model import _chunks

    values = input_values(texts)
    summaries = [
        json.loads(value)
        for message in body["messages"]
        if message["role"] == "assistant" and isinstance(message.get("content"), str)
        for value in re.findall(r"^RECOVERY_SUMMARY (\{[^\n]+\})$", message["content"], re.MULTILINE)
    ]
    prior = summaries[-1] if summaries else {}
    compacting = _COMPACTION_PROMPT in texts
    observation = {"compacting": compacting, "inputs": values, "messages": body["messages"]}
    async with await anyio.Path(path / "recovery-observations.jsonl").open("a") as output:
        await output.write(json.dumps(observation) + "\n")
    delegated = prior.get("delegated", False) or any(
        "execution_id" in str(message.get("content")) for message in tool_messages
    )
    stepped = prior.get("stepped", False) or any(
        "recovery-step-complete" in str(message.get("content")) for message in tool_messages
    )
    count = len(values) if values else prior.get("count", 0)
    tool, answer = None, ""
    if compacting:
        summary = {
            "count": count,
            "digest": hashlib.sha256("\n".join(values).encode()).hexdigest() if values else prior.get("digest", ""),
            "delegated": delegated,
            "stepped": stepped,
        }
        # Preserve routing and a derived summary, deliberately not the original
        # input text or provenance. Only the Host receipt can prove consumption.
        answer = "LIVE_TEST " + case.model_dump_json() + "\nRECOVERY_SUMMARY " + json.dumps(summary)
    elif any(re.search(r"^RECOVERY_CHILD$", text, re.MULTILINE) for text in texts):
        await wait_file(path, "child_ready", "child_release")
        answer = "RECOVERY_CHILD_RESULT " + case.token
    elif case.scenario == "recovery_child" and not delegated:
        tool = tool_call(
            body,
            "delegate",
            {"subagent_name": "child", "prompt": "LIVE_TEST " + case.model_dump_json() + "\nRECOVERY_CHILD"},
        )
    elif case.scenario == "recovery_handoff":
        steps = sum("handoff-step-" in str(message.get("content")) for message in tool_messages)
        if steps == 2:
            answer = "handoffs-finished"
        else:
            tool = tool_call(body, "live_recovery_handoff_step", {"case_id": case.case_id, "step": steps + 1})
    elif case.scenario == "recovery_effect":
        if any("effect-confirmed" in str(message.get("content")) for message in tool_messages):
            answer = "effect-confirmed"
        else:
            plan = json.loads((path / "plan.json").read_text())
            tool = tool_call(body, "live_recovery_effect", {"case_id": case.case_id, "idempotent": plan["idempotent"]})
    elif not stepped:
        tool = tool_call(body, "live_recovery_step", {"case_id": case.case_id})
    else:
        answer = f"received:{count}"
    return StreamingResponse(_chunks(case, path, answer, tool), media_type="text/event-stream")
