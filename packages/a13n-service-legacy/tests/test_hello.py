from importlib.metadata import version

from a13n_service_legacy import __version__


def test_package_exposes_distribution_version() -> None:
    assert __version__ == version("a13n-service")
