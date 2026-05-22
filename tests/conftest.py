"""Pytest configuration for the sources module."""


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests (hit real network)."
    )
