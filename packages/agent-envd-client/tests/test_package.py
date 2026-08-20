from importlib.metadata import version

import converge_agent_envd_client


def test_package_version_matches_distribution() -> None:
    assert converge_agent_envd_client.__version__ == version("converge-agent-envd-client")
