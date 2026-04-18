"""Shared pytest fixtures for the Anker Charger test suite.

Later phases will add fixtures for MockConfigEntry, a stubbed
AnkerSolixApiClient, and decoded MQTT payloads. For now this just
enables the pytest-homeassistant-custom-component `enable_custom_integrations`
auto-use hook so HA recognises `custom_components/anker_charger` during
harness tests.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA discover the local custom component in every test."""
    yield
