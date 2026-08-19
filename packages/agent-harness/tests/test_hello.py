from converge_agent_harness import hello


def test_hello() -> None:
    assert hello() == "Hello from converge-agent-harness!"
