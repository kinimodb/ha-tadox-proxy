"""Shared fixtures for the Home Assistant integration tests.

These tests load the integration into a real Home Assistant core.  The
harness comes from pytest-homeassistant (Home Assistant 2026.11 and newer) or
pytest-homeassistant-custom-component (older), see harness.py.  They need
Linux or macOS (Home Assistant does not run on Windows) and stay separate
from tests/, which must keep running without Home Assistant installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_CUSTOM_COMPONENTS = str(Path(__file__).resolve().parents[1] / "custom_components")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make the repo's integration loadable by the test Home Assistant."""
    # With pytest-homeassistant-custom-component the hass fixture mounts the
    # harness's own regular ``custom_components`` package (its testing_config),
    # which hides ours – add our directory to it.  pytest-homeassistant mounts
    # none, so ours is found already and nothing is added.
    import custom_components

    if REPO_CUSTOM_COMPONENTS not in custom_components.__path__:
        custom_components.__path__.append(REPO_CUSTOM_COMPONENTS)
    yield
