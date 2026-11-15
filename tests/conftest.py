"""Planner tests run anywhere; HA tests need pytest-homeassistant-custom-component."""

import importlib.util
from pathlib import Path

import pytest

HAS_HA = importlib.util.find_spec("pytest_homeassistant_custom_component") is not None
REPO_CUSTOM_COMPONENTS = str(Path(__file__).parent.parent / "custom_components")

collect_ignore = [] if HAS_HA else ["test_init.py"]

if HAS_HA:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        # The test harness puts its own testing_config/custom_components package
        # first; add this repository's folder so HA can find our integration.
        import custom_components

        if REPO_CUSTOM_COMPONENTS not in custom_components.__path__:
            custom_components.__path__.append(REPO_CUSTOM_COMPONENTS)
        yield
