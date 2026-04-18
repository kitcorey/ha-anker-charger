"""Tests for ``SolixMqttDeviceCharger``.

These instantiate a charger MQTT device against an in-memory ``AnkerSolixApi``
(no network, no broker) and verify the mapping of A91B2 FEATURES through
the SOLIXMQTTMAP setup into callable controls.
"""

from __future__ import annotations

import pytest

from custom_components.anker_charger.solixapi.api import AnkerSolixApi
from custom_components.anker_charger.solixapi.mqtt_charger import (
    SolixMqttDeviceCharger,
)
from custom_components.anker_charger.solixapi.mqttcmdmap import (
    SolixMqttCommands,
)


@pytest.fixture
def api_with_charger() -> AnkerSolixApi:
    """Return an API with one A91B2 charger registered (no real login)."""
    api = AnkerSolixApi(email="tester@example.com", password="x", countryId="US")
    api._update_dev(
        {
            "device_sn": "AFCJTB0F00000001",
            "ms_device_type": 1,
            "product_code": "A91B2",
            "device_name": "240W Charging Station",
            "alias_name": "Test Charger",
        }
    )
    return api


@pytest.fixture
def charger(api_with_charger: AnkerSolixApi) -> SolixMqttDeviceCharger:
    """Build a charger MQTT device bound to the seeded API."""
    return SolixMqttDeviceCharger(
        api_instance=api_with_charger,
        device_sn="AFCJTB0F00000001",
    )


class TestChargerFeatureWiring:
    def test_models_set_to_a91b2(self, charger: SolixMqttDeviceCharger):
        assert charger.models == {"A91B2"}

    def test_device_pn_populated(self, charger: SolixMqttDeviceCharger):
        assert charger.pn == "A91B2"

    def test_features_include_ac_outlet_switches(
        self, charger: SolixMqttDeviceCharger
    ):
        assert SolixMqttCommands.ac_1_port_switch in charger.features
        assert SolixMqttCommands.ac_2_port_switch in charger.features


class TestControlsSetup:
    def test_ac_outlet_switch_controls_registered(
        self, charger: SolixMqttDeviceCharger
    ):
        # Controls populated by _setup_controls from SOLIXMQTTMAP; A91B2's
        # 0207 message wires these two commands in.
        assert SolixMqttCommands.ac_1_port_switch in charger.controls
        assert SolixMqttCommands.ac_2_port_switch in charger.controls

    def test_ac_outlet_command_uses_0207_msg_type(
        self, charger: SolixMqttDeviceCharger
    ):
        ctrl = charger.controls[SolixMqttCommands.ac_1_port_switch]
        assert ctrl["msg_type"] == "0207"

    def test_unrelated_command_not_registered(
        self, charger: SolixMqttDeviceCharger
    ):
        # FEATURES also lists port_memory_switch and display_switch, but the
        # A91B2 map doesn't describe message types for them — so they should
        # not show up as controls. mqtt_charger.FEATURES documents this
        # "silently ignored" contract.
        assert SolixMqttCommands.display_switch not in charger.controls
        assert SolixMqttCommands.port_memory_switch not in charger.controls


class TestCmdIsSwitch:
    def test_returns_false_for_unknown_command(
        self, charger: SolixMqttDeviceCharger
    ):
        assert charger.cmd_is_switch("no_such_command") is False

    def test_returns_false_for_non_string(self, charger: SolixMqttDeviceCharger):
        assert charger.cmd_is_switch(None) is False
        assert charger.cmd_is_switch(42) is False


class TestIsSubscribed:
    def test_false_without_connected_mqtt(
        self, charger: SolixMqttDeviceCharger
    ):
        # No MQTT session started, so there's no subscription to match.
        assert charger.is_subscribed() is False
