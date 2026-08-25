from __future__ import annotations

from pathlib import Path

import pytest

from converge_local_agent_example import run_local_agent_demo

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("review_style", ["Focused", "Broad"])
async def test_local_agent_runs_real_tools_then_resumes_with_fresh_bindings(
    tmp_path: Path,
    review_style: str,
) -> None:
    result = await run_local_agent_demo(tmp_path, review_style=review_style)

    assert result.first_run_id != result.resumed_run_id
    assert result.output == f"Local plan created; {review_style.lower()} review selected."
    assert result.tool_calls == ("write", "task_create", "note", "ask_user_question")
    assert result.plan == "# Local plan\n\nCreated by a managed Direct Local file tool.\n"
    assert (tmp_path / "plan.md").read_text() == result.plan
