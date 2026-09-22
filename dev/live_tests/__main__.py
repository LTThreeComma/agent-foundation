"""Run the disposable public Service journey through its installed CLI and HTTPS API."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "pytest", "packages/a13n-service/tests/test_live_process.py", "-q", *sys.argv[1:]],
            cwd=root,
        )
    )


if __name__ == "__main__":
    main()
