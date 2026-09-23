"""Run the Service live journeys: `python -m dev.live_tests [pytest arguments]`.

The report lists every journey with its outcome and names each skipped journey with its reason. Pass
`--require-all` to fail, instead of skip, a journey whose external dependency is unavailable.
"""

import sys
from pathlib import Path

import pytest

SUITE = Path(__file__).resolve().parent
# Testcontainers 4.13 deprecates its own readiness decorator on import; the notice says nothing about the suite.
TESTCONTAINERS_NOTICE = "The @wait_container_is_ready decorator is deprecated"


def main() -> None:
    arguments = [str(SUITE), "-v", "-rfEs", "--durations=0", "-W", f"ignore:{TESTCONTAINERS_NOTICE}:DeprecationWarning"]
    raise SystemExit(pytest.main([*arguments, *sys.argv[1:]]))


if __name__ == "__main__":
    main()
