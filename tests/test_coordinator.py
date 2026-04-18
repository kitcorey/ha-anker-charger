"""Tests for ``AnkerSolixDataUpdateCoordinator``."""

from __future__ import annotations

from custom_components.anker_charger.const import DOMAIN
from tests.conftest import (
    ACCOUNT_EMAIL,
    CHARGER_SN_LIVING_ROOM,
    CHARGER_SN_SUNROOM,
)


async def test_coordinator_data_populated_on_first_refresh(hass, setup_entry):
    """After setup the coordinator should hold the full canned data dict."""
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    assert set(coordinator.data) == {
        CHARGER_SN_SUNROOM,
        CHARGER_SN_LIVING_ROOM,
        ACCOUNT_EMAIL,
    }


async def test_registered_devices_seeded(hass, setup_entry):
    """First-refresh should register both charger SNs in the tracking set."""
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    assert CHARGER_SN_SUNROOM in coordinator.registered_devices
    assert CHARGER_SN_LIVING_ROOM in coordinator.registered_devices


async def test_update_callback_triggers_data_refresh(hass, setup_entry):
    """The MQTT update callback should cause the coordinator to re-publish state."""
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]

    # Fire the callback synchronously (as paho would from its own thread).
    coordinator.update_callback(sn=CHARGER_SN_SUNROOM)
    await hass.async_block_till_done()

    # After the callback, the stubbed from_cache client call should have
    # been invoked at least once.
    assert coordinator.client.async_get_data.await_count >= 1
