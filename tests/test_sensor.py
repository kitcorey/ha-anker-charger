"""Tests for the sensor platform."""

from __future__ import annotations


async def test_firmware_entity_state(hass, setup_entry):
    """Firmware sensor should surface the sw_version from cloud data."""
    state = hass.states.get("sensor.sunroom_charging_station_firmware")
    assert state is not None
    assert state.state == "v1.1.2.4"


async def test_wifi_signal_entity_with_rssi_attribute(hass, setup_entry):
    """Wi-Fi signal sensor should expose the rssi attribute."""
    state = hass.states.get("sensor.sunroom_charging_station_wi_fi_signal")
    assert state is not None
    assert state.state == "85"
    assert state.attributes.get("rssi") == "-58"


async def test_usb_port_power_entities_created(hass, setup_entry):
    """Each A91B2 should expose four USB-C + two USB-A power sensors."""
    for port in ("usb_c_port_1", "usb_c_port_2", "usb_c_port_3", "usb_c_port_4",
                 "usb_a_port_1", "usb_a_port_2"):
        assert hass.states.get(
            f"sensor.sunroom_charging_station_{port}"
        ) is not None, f"missing sensor for {port}"


async def test_usbc_1_port_power_value(hass, setup_entry):
    """USB-C port 1 sensor should reflect the MQTT-snapshot power value."""
    state = hass.states.get("sensor.sunroom_charging_station_usb_c_port_1")
    assert state is not None
    assert float(state.state) == 7.5


async def test_mqtt_time_sensor_created(hass, setup_entry):
    """The mqtt_timestamp sensor should appear when MQTT data is cached."""
    state = hass.states.get("sensor.sunroom_charging_station_mqtt_time")
    assert state is not None
