from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SCRIPTS_DIRECTORY = Path(__file__).parents[1]
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

from prepare_agent_ui_assets import prepare_assets  # type: ignore[import-not-found]  # noqa: E402


def test_prepares_deterministic_asset_manifest(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    source.mkdir()
    (source / "index.html").write_text("<h1>Agent UI</h1>\n", encoding="utf-8")
    assets = source / "assets"
    assets.mkdir()
    (assets / "main.js").write_bytes(b"console.log('agent-ui')\n")
    target = tmp_path / "static"

    prepare_assets(source, target)

    manifest = json.loads((target / "asset-manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "schema_version": "1",
        "source": "apps/harness-ui",
        "files": {
            "assets/main.js": hashlib.sha256(b"console.log('agent-ui')\n").hexdigest(),
            "index.html": hashlib.sha256(b"<h1>Agent UI</h1>\n").hexdigest(),
        },
    }


def test_replaces_stale_target_contents(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    source.mkdir()
    (source / "index.html").write_text("new\n", encoding="utf-8")
    target = tmp_path / "static"
    target.mkdir()
    (target / "stale.js").write_text("stale\n", encoding="utf-8")

    prepare_assets(source, target)

    assert not (target / "stale.js").exists()
    assert (target / "index.html").read_text(encoding="utf-8") == "new\n"
