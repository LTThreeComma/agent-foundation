"""The seeded local state: fictional resources and runs created through the public API, then read back.

Runs execute for real against the local scripted model, whose prompt markers (`[client]`, `[fail]`) select
its behavior; see `dev/fixtures/model.py`.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from dev.service.api import Api, Json
from dev.service.checkout import ADMIN_EMAIL, ADMIN_PASSWORD

MODEL_KEY = "local-scripted"
SKILL = b"""---
name: release-notes
description: Turn a list of changes into short, factual release notes.
---
# Release notes

Group changes under Added, Changed and Fixed, one line each, without marketing language.
"""
REVIEW_TOOL = {
    "name": "local_review",
    "description": "Ask the user to review a draft; they return a decision and a short reason.",
    "parameters_json_schema": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]},
}


def seed(api: Api, model_url: str) -> Json:
    """Create the seeded state in the default workspace; returns what was created."""
    workspace = api.items("/api/v1/workspaces")[0]
    org, ws = f"/api/v1/organizations/{workspace['organization_id']}", f"/api/v1/workspaces/{workspace['id']}"
    provider = api.post(
        f"{org}/model-providers",
        {
            "workspace_id": None,
            "type": "openai",
            "name": "Local scripted model",
            "config": {"base_url": model_url},
            "credential": {"api_key": "local-scripted"},
        },
    )
    model = api.post(
        f"{org}/models",
        {
            "workspace_id": None,
            "provider_id": provider["id"],
            "key": MODEL_KEY,
            "name": "Local scripted model",
            "config": {"model_name": MODEL_KEY, "model_api": "openai.chat_completions"},
        },
    )
    upload = api.post(
        f"{ws}/uploads", files={"file": ("release-notes.zip", _package(), "application/zip")}, idempotent=True
    )
    # Skills mount into an environment, and the local seed offers none; the skill stands on its own.
    api.post(f"{ws}/skills", {"source": {"kind": "upload", "upload_id": upload["upload_id"]}})
    writer = _agent(api, ws, "release-writer", "Release writer", model)
    reviewer = _agent(api, ws, "release-reviewer", "Release reviewer", model, client_tools=[REVIEW_TOOL])
    api.post(f"{ws}/configuration-assistant")

    first = _start(api, ws, writer, "Draft release notes for the fictional Orbit 2.4 release.")
    reply = api.post(f"{ws}/threads/{first['thread_id']}/inbox", _message(writer, "Shorter, please."), idempotent=True)
    api.sealed_run(ws, reply["run"]["id"])
    waiting = _start(api, ws, reviewer, "[client] Review the Orbit 2.4 notes before they are published.")
    failed = _start(api, ws, writer, "[fail] Summarize the fictional incident report.")
    return {
        "organization": org,
        "workspace": ws,
        "model": model["id"],
        "conversation": first["thread_id"],
        "waiting_run": waiting["id"],
        "failed_run": failed["id"],
    }


def verify(api: Api, seeded: Json) -> list[tuple[str, bool]]:
    """Read the seeded state back through the API; each check names what the Console should show."""
    org, ws = seeded["organization"], seeded["workspace"]
    agents = {agent["key"] for agent in api.items(f"{ws}/agents")}
    conversation = api.items(f"{ws}/threads/{seeded['conversation']}/runs")
    waiting = api.get(f"{ws}/runs/{seeded['waiting_run']}")
    pending = [item["tool_name"] for item in (waiting.get("pending") or {}).get("items", [])]
    items = api.get(f"{ws}/runs/{conversation[0]['id']}/items")["items"] if conversation else []
    return [
        ("The scripted model is enabled", api.get(f"{org}/models/{seeded['model']}")["enabled"]),
        ("The release-notes skill exists", "release-notes" in {skill["key"] for skill in api.items(f"{ws}/skills")}),
        (
            "Writer, reviewer and configuration assistant exist",
            {"release-writer", "release-reviewer", "configuration-assistant"} <= agents,
        ),
        ("The conversation holds two completed runs", [run["status"] for run in conversation] == ["completed"] * 2),
        (
            "The conversation shows the assistant's reply",
            any(item["content"].get("role") == "assistant" for item in items),
        ),
        ("The review run waits for local_review", waiting["status"] == "waiting" and pending == ["local_review"]),
        ("The incident run failed", api.get(f"{ws}/runs/{seeded['failed_run']}")["status"] == "failed"),
    ]


def write_report(path: Path, console_url: str, seeded: Json, checks: list[tuple[str, bool]]) -> None:
    lines = [
        "# Seeded local state",
        "",
        f"Sign in at {console_url} as `{ADMIN_EMAIL}` / `{ADMIN_PASSWORD}`.",
        "",
        *(f"- {name}: `{value}`" for name, value in seeded.items()),
        "",
        "## Verification",
        "",
        *(f"- [{'x' if passed else ' '}] {name}" for name, passed in checks),
        "",
    ]
    path.write_text("\n".join(lines))


def _package() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("release-notes/SKILL.md", SKILL)
    return output.getvalue()


def _agent(api: Api, ws: str, key: str, name: str, model: Json, **config: object) -> Json:
    instructions = f"You are the {name.lower()} of a fictional product team. Keep answers short."
    body = {
        "key": key,
        "name": name,
        "config": {"model": {"model_id": model["id"]}, "instructions": instructions, **config},
    }
    return api.post(f"{ws}/agents", body)


def _message(agent: Json, text: str) -> Json:
    return {"agent_id": agent["id"], "payload": {"content": [{"type": "text", "text": text}]}}


def _start(api: Api, ws: str, agent: Json, text: str) -> Json:
    """Start a thread and return its first run once sealed."""
    started = api.post(f"{ws}/threads", _message(agent, text), idempotent=True)
    return api.sealed_run(ws, started["run"]["id"])
