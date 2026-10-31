"""Smoke test that the package is importable from the test environment."""

import emporio_agent


def test_package_exposes_version() -> None:
    """The package imports and exposes a semantic version string."""
    assert emporio_agent.__version__.count(".") == 2
