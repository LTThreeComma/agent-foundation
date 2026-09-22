"""The new deployment workflow must not reuse legacy stores or credentials."""

import subprocess
import sys
from pathlib import Path


def test_unimplemented_rollout_refuses_before_changing_state(tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/k8s_local.py"), "up"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "existing cluster stores were not touched" in result.stderr
    assert not list(tmp_path.iterdir())
