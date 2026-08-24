from importlib.metadata import version

from converge_agent_stream_protocol import __version__


def test_package_exposes_distribution_version() -> None:
    assert __version__ == version("converge-agent-stream-protocol")
