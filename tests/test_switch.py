"""Tests for the switch platform."""

from __future__ import annotations


async def test_ac_outlet_switches_created(hass, setup_entry):
    """Both AC outlet switches should register per charger."""
    assert hass.states.get("switch.sunroom_charging_station_ac_outlet_1") is not None
    assert hass.states.get("switch.sunroom_charging_station_ac_outlet_2") is not None
    assert (
        hass.states.get("switch.living_room_charging_station_ac_outlet_1") is not None
    )
    assert (
        hass.states.get("switch.living_room_charging_station_ac_outlet_2") is not None
    )


async def test_ac_outlet_1_reflects_mqtt_state(hass, setup_entry):
    """AC outlet 1 is ``on`` in the fixture snapshot (ac_1_switch=1)."""
    state = hass.states.get("switch.sunroom_charging_station_ac_outlet_1")
    assert state is not None
    assert state.state == "on"


async def test_ac_outlet_2_off_in_fixture(hass, setup_entry):
    """AC outlet 2 is ``off`` in the fixture snapshot (ac_2_switch=0)."""
    state = hass.states.get("switch.sunroom_charging_station_ac_outlet_2")
    assert state is not None
    assert state.state == "off"


async def test_ac_outlet_toggle_invokes_run_command(hass, setup_entry, mock_api_client):
    """Toggling an AC outlet switch should dispatch run_command on the MQTT device."""
    mdev = mock_api_client.get_mqtt_device("AFCJTB0F29104842")
    mdev.run_command.reset_mock()

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.sunroom_charging_station_ac_outlet_1"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert mdev.run_command.await_count >= 1


async def test_api_usage_account_switch_present(hass, setup_entry):
    """The account-level API-usage switch should register on setup."""
    # Title "tester" + country "US" → slug "tester_us_api_usage".
    state = hass.states.get("switch.tester_us_api_usage")
    assert state is not None
    assert state.state == "on"
