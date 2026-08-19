from converge_foundation_service import hello


def test_hello() -> None:
    assert hello() == "Hello from converge-foundation-service!"
