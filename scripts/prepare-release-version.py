from __future__ import annotations

import argparse
from pathlib import Path

from release_notes import ReleaseNotesError, read_manual_release_notes, release_notes_path
from release_version import COMPONENTS, ReleaseVersionError, prepare_component_version


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject a release version into known manifests and lockfiles.")
    parser.add_argument("component", choices=COMPONENTS)
    parser.add_argument("version")
    args = parser.parse_args()

    root = Path.cwd()
    try:
        manual_notes = read_manual_release_notes(root, args.component, args.version)
        changed = prepare_component_version(root, args.component, args.version)
    except (ReleaseNotesError, ReleaseVersionError) as error:
        raise SystemExit(str(error)) from error

    if manual_notes is not None:
        print(f"Validated curated notes at {release_notes_path(args.component, args.version)}")
    if changed:
        print(f"Prepared {args.component} version {args.version}:")
        for path in changed:
            print(f"- {path}")
    else:
        print(f"Prepared {args.component} version {args.version}; no files changed")


if __name__ == "__main__":
    main()
