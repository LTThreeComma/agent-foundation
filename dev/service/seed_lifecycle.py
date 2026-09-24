"""Resources that change after runs used them: earlier runs keep the revisions they pinned."""

from __future__ import annotations

from dev.service.api import Json
from dev.service.seed_agents import Cast
from dev.service.seed_conversations import Talk
from dev.service.seed_resources import SKILLS, publish


def revise_after_runs(talk: Talk, cast: Cast, skills: dict[str, Json], last_run: Json) -> dict[str, str]:
    """Publish new revisions of the release-notes skill and the writer, whose conversation continues on the new
    default and once more pinned to its first revision; also a newer revision that is not the default."""
    api, ws = talk.api, talk.ws
    notes = next(skill for skill in SKILLS if skill.key == "release-notes")
    skill_path = f"{ws}/skills/{skills[notes.key]['id']}"
    api.post(
        f"{skill_path}/revisions",
        {"source": publish(api, ws, notes, revision=2), "note": "Adds a checklist"},
        current=api.get(skill_path),
    )
    path = f"{ws}/agents/{cast.writer['id']}"
    first = api.get(path)["default_revision_id"]
    config = api.get(f"{path}/revisions/{first}")["config"]
    # The configuration keeps its pinned skill revision, so the writer still reads the skill's first revision.
    revised = {**config, "instructions": "Write release notes with explicit trade-offs. Keep answers short."}
    second = api.post(f"{path}/revisions", {"config": revised, "note": "Explicit trade-offs"}, current=api.get(path))
    draft = {**revised, "instructions": revised["instructions"] + " Prefer a friendly tone."}
    api.post(
        f"{path}/revisions",
        {"config": draft, "note": "Draft: friendlier tone", "make_default": False},
        current=api.get(path),
    )
    duplicate = api.post(
        f"{path}/duplicate", {"key": "release-writer-copy", "name": "Release writer (copy)", "revision_id": first}
    )
    latest = talk.reply(last_run, cast.writer, "One more pass for the 2.4.1 patch release.")
    pinned = talk.reply(latest, cast.writer, "Compare with the original wording.", agent_revision_id=first)
    return {
        "writer_first_revision": first,
        "writer_default_revision": second["id"],
        "writer_duplicate": duplicate["id"],
        "run_on_new_default": latest["id"],
        "run_pinned_to_first_revision": pinned["id"],
    }
