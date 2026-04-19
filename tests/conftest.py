"""Shared pytest fixtures for the Anker Charger test suite.

The HA-harness tests in ``tests/test_*.py`` lean on the fixtures defined
here to stand up a ``MockConfigEntry`` against a stubbed
``AnkerSolixApiClient`` without touching the Anker cloud or a real MQTT
broker.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anker_charger.const import (
    CONF_MQTT_OPTIONS,
    CONF_MQTT_USAGE,
    CONF_TRIGGER_TIMEOUT,
    DOMAIN,
)

ACCOUNT_EMAIL = "tester@example.com"
CHARGER_SN_SUNROOM = "AFCJTB0F29104842"
CHARGER_SN_LIVING_ROOM = "AFCJTB0F08102349"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA discover the local custom component in every test."""
    yield


def _charger_device(sn: str, alias: str) -> dict[str, Any]:
    """Return a charger device dict shaped like what ``get_bind_devices`` populates."""
    return {
        "device_sn": sn,
        "type": "charger",
        "device_pn": "A91B2",
        "name": "240W Charging Station",
        "alias": alias,
        "is_admin": True,
        "owner_user_id": "user-abc",
        "mqtt_supported": True,
        "mqtt_overlay": False,
        "mqtt_status_request": True,
        "sw_version": "v1.1.2.4",
        "wifi_online": True,
        "wifi_signal": "85",
        "rssi": "-58",
    }


def _mqtt_snapshot() -> dict[str, Any]:
    """Return a synthetic MQTT combined-cache snapshot for one charger."""
    return {
        "last_update": "2026-04-18 12:00:00",
        "msg_timestamp": 1776600000,
        "usbc_1_status": 1,
        "usbc_1_voltage": "5.000",
        "usbc_1_current": "1.500",
        "usbc_1_power": "7.500",
        "usbc_2_status": 0,
        "usbc_2_voltage": "0.000",
        "usbc_2_current": "0.000",
        "usbc_2_power": "0.000",
        "usbc_3_status": 0,
        "usbc_3_voltage": "0.000",
        "usbc_3_current": "0.000",
        "usbc_3_power": "0.000",
        "usbc_4_status": 0,
        "usbc_4_voltage": "0.000",
        "usbc_4_current": "0.000",
        "usbc_4_power": "0.000",
        "usba_1_status": 0,
        "usba_1_voltage": "5.000",
        "usba_1_current": "0.000",
        "usba_1_power": "0.000",
        "usba_2_status": 0,
        "usba_2_voltage": "0.000",
        "usba_2_current": "0.000",
        "usba_2_power": "0.000",
        "ac_1_switch": 1,
        "ac_2_switch": 0,
    }


def _account_data() -> dict[str, Any]:
    """Return a plausible account-level data entry for the coordinator cache."""
    return {
        "type": "account",
        "email": ACCOUNT_EMAIL,
        "nickname": "tester",
        "country": "US",
        "server": "https://ankerpower-api.anker.com",
        "requests_last_min": 2,
        "requests_last_hour": 17,
        "mqtt_connection": True,
        "mqtt_statistic": {
            "start_time": "2026-04-18 11:00",
            "bytes_received": 12345,
            "bytes_sent": 500,
            "kb_hourly_received": 12.3,
            "kb_hourly_sent": 0.5,
            "dev_messages": {"count": 42},
        },
    }


@pytest.fixture
def coordinator_data() -> dict[str, dict]:
    """Return the ``client.async_get_data`` result for a two-charger account."""
    return {
        CHARGER_SN_SUNROOM: _charger_device(
            CHARGER_SN_SUNROOM, "Sunroom Charging Station"
        ),
        CHARGER_SN_LIVING_ROOM: _charger_device(
            CHARGER_SN_LIVING_ROOM, "Living Room Charging Station"
        ),
        ACCOUNT_EMAIL: _account_data(),
    }


@pytest.fixture
def mqtt_snapshot() -> dict[str, Any]:
    """MQTT combined-cache snapshot (shared across both chargers)."""
    return _mqtt_snapshot()


def _mock_mqtt_device(snapshot: dict) -> MagicMock:
    """Stand-in for ``SolixMqttDevice`` with a valid ``get_combined_cache``."""
    mdev = MagicMock()
    mdev.mqttdata = snapshot
    mdev.device = {}
    mdev.is_subscribed = MagicMock(return_value=True)
    mdev.get_combined_cache = MagicMock(return_value=snapshot)
    mdev.run_command = AsyncMock(return_value={"ac_1_switch": 0})
    mdev.status_request = AsyncMock()
    mdev.realtime_trigger = AsyncMock()
    return mdev


@pytest.fixture
def mock_api_client(coordinator_data, mqtt_snapshot) -> MagicMock:
    """Return a fully-stubbed ``AnkerSolixApiClient`` for HA harness tests."""
    client = MagicMock()
    client.authenticate = AsyncMock(return_value=True)
    client.async_get_data = AsyncMock(return_value=coordinator_data)
    client.allow_refresh = MagicMock(return_value=True)
    client.mqtt_usage = AsyncMock(return_value=True)
    client.trigger_timeout = MagicMock(return_value=300)
    client.check_mqtt_session = AsyncMock()
    client.validate_cache = AsyncMock(return_value=True)
    client.request = AsyncMock(return_value={"code": 0, "data": {}})

    sunroom_mdev = _mock_mqtt_device(mqtt_snapshot)
    living_mdev = _mock_mqtt_device(mqtt_snapshot)
    mdev_map = {
        CHARGER_SN_SUNROOM: sunroom_mdev,
        CHARGER_SN_LIVING_ROOM: living_mdev,
    }
    client.mqtt_devices = mdev_map
    client.get_mqtt_device = MagicMock(side_effect=lambda sn=None: mdev_map.get(sn))
    client.get_mqtt_devices = MagicMock(return_value=list(mdev_map.values()))
    client.get_mqtt_valuecount = MagicMock(return_value=sum(
        len(m.mqttdata) for m in mdev_map.values()
    ))

    # Attach a minimal ``api`` sub-mock used by coordinator + platforms.
    api = MagicMock()
    api.apisession = MagicMock()
    api.apisession.email = ACCOUNT_EMAIL
    api.apisession.nickname = "tester"
    api.apisession.request_count = MagicMock(
        last_minute=MagicMock(return_value=2),
        last_hour=MagicMock(return_value=17),
    )
    api.mqttsession = None  # "connected" state is mocked via get_mqtt_device
    api.devices = {
        CHARGER_SN_SUNROOM: coordinator_data[CHARGER_SN_SUNROOM],
        CHARGER_SN_LIVING_ROOM: coordinator_data[CHARGER_SN_LIVING_ROOM],
    }
    api.account = coordinator_data[ACCOUNT_EMAIL]
    api.clearCaches = MagicMock()
    api.getCaches = MagicMock(return_value=coordinator_data)
    api.startMqttSession = AsyncMock(return_value=True)
    api.stopMqttSession = MagicMock()
    api.mqtt_update_callback = MagicMock(return_value=None)
    api.update_device_details = AsyncMock(return_value=api.devices)
    api.update_sites = AsyncMock(return_value={})
    api.request_count = api.apisession.request_count
    client.api = api
    return client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry matching what the user-flow produces after setup."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_EMAIL,
        title="tester",
        data={
            "username": ACCOUNT_EMAIL,
            "password": "pw",
            "country_code": "US",
            "accept_terms": True,
            "nickname": "tester",
        },
        options={
            "scan_interval": 60,
            CONF_MQTT_OPTIONS: {
                CONF_MQTT_USAGE: True,
                CONF_TRIGGER_TIMEOUT: 300,
            },
        },
        version=2,
        minor_version=1,
    )


@pytest.fixture
async def setup_entry(
    hass,
    mock_api_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> Iterator[MockConfigEntry]:
    """Stand up the integration with the stubbed client and yield the entry."""
    # Preserve the real ``scan_interval_from`` classmethod — __init__.py reads
    # it to size the coordinator's update_interval, and a MagicMock return
    # there crashes the timedelta constructor.
    from custom_components.anker_charger.api_client import AnkerSolixApiClient

    with patch(
        "custom_components.anker_charger.api_client.AnkerSolixApiClient",
        return_value=mock_api_client,
    ) as cls_mock:
        cls_mock.scan_interval_from = AnkerSolixApiClient.scan_interval_from
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        yield mock_config_entry
