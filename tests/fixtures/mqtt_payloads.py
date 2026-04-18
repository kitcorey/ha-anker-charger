"""Synthetic A91B2 MQTT payload builders for decoder tests.

These payloads are assembled by hand from known byte layouts documented in
``solixapi/mqttmap.py`` and exercised through ``DeviceHexData``. Every helper
returns a ``(hex_string, expected_values)`` pair so tests can assert the
decoded dict against known-good values, not just round-trip through the
encoder.
"""

from __future__ import annotations

import struct

from custom_components.anker_charger.solixapi.mqtttypes import (
    DeviceHexData,
    DeviceHexDataField,
)


def _build(
    msgtype: tuple[int, int],
    fields: dict[str, tuple[int, bytes]],
) -> str:
    """Assemble a full A91B2 message with the given msgtype and fields.

    ``fields`` maps field-name hex (e.g. ``"a4"``) to (f_type_byte, f_value_bytes).
    Returns the full hex string (prefix + header + fields + checksum).
    """
    hd = DeviceHexData(model="A91B2")
    hd.msg_header.prefix = bytearray([0xFF, 0x09])
    hd.msg_header.pattern = bytearray([0x03, 0x01, 0x0F])
    hd.msg_header.msgtype = bytearray(msgtype)
    hd.msg_fields = {
        name: DeviceHexDataField(
            f_name=bytearray(bytes.fromhex(name)),
            f_type=bytearray([ftype]),
            f_value=bytearray(fvalue),
        )
        for name, (ftype, fvalue) in fields.items()
    }
    hd._update_hexbytes()
    return hd.hex()


def _usb_port_bytes(status: int, voltage_mv: int, current_ma: int, power_cw: int) -> bytes:
    """Build the 7-byte strb payload for one USB port.

    Layout per mqttmap ``_USB_PORT_CONSUMPTION`` and the A91B2 0a00 a4..a9
    fields: status (1B ui) + voltage (2B sile ×0.001) + current (2B sile
    ×0.001) + power (2B sile ×0.01). Values are in the scaled units the
    decoder produces after the FACTOR multiplication.
    """
    return (
        bytes([status])
        + struct.pack("<h", voltage_mv)
        + struct.pack("<h", current_ma)
        + struct.pack("<h", power_cw)
    )


def status_0a00(
    *,
    usbc_1: tuple[int, int, int, int] = (0, 0, 0, 0),
    usbc_2: tuple[int, int, int, int] = (0, 0, 0, 0),
    usbc_3: tuple[int, int, int, int] = (0, 0, 0, 0),
    usbc_4: tuple[int, int, int, int] = (0, 0, 0, 0),
    usba_1: tuple[int, int, int, int] = (0, 0, 0, 0),
    usba_2: tuple[int, int, int, int] = (0, 0, 0, 0),
    ac_1_switch: int = 0,
    ac_2_switch: int = 0,
    msg_timestamp: int = 1776485917,
) -> tuple[str, dict]:
    """Full device status (0a00) payload and its expected decoded values."""
    fields = {
        "a2": (0x03, bytes([1, 2, 3, 0])),  # sw_version raw bytes
        "a4": (0x06, _usb_port_bytes(*usbc_1)),
        "a5": (0x06, _usb_port_bytes(*usbc_2)),
        "a6": (0x06, _usb_port_bytes(*usbc_3)),
        "a7": (0x06, _usb_port_bytes(*usbc_4)),
        "a8": (0x06, _usb_port_bytes(*usba_1)),
        "a9": (0x06, _usb_port_bytes(*usba_2)),
        "aa": (0x06, bytes([ac_1_switch])),
        "ab": (0x06, bytes([ac_2_switch])),
        "fe": (0x03, msg_timestamp.to_bytes(4, "little")),
    }
    hex_str = _build((0x0A, 0x00), fields)

    expected: dict = {
        "sw_version": "1.9.7.1.2.1",  # Observed output of var+values=3 path (int LE → '.'.join)
        "ac_1_switch": ac_1_switch,
        "ac_2_switch": ac_2_switch,
        "msg_timestamp": msg_timestamp,
    }
    for port_name, port_values in [
        ("usbc_1", usbc_1),
        ("usbc_2", usbc_2),
        ("usbc_3", usbc_3),
        ("usbc_4", usbc_4),
        ("usba_1", usba_1),
        ("usba_2", usba_2),
    ]:
        status, voltage_mv, current_ma, power_cw = port_values
        expected[f"{port_name}_status"] = status
        expected[f"{port_name}_voltage"] = round(voltage_mv * 0.001, 3)
        expected[f"{port_name}_current"] = round(current_ma * 0.001, 3)
        expected[f"{port_name}_power"] = round(power_cw * 0.01, 2)

    return hex_str, expected


def realtime_0303(
    *,
    usbc_1: tuple[int, int, int, int] = (0, 0, 0, 0),
    usbc_2: tuple[int, int, int, int] = (0, 0, 0, 0),
    usbc_3: tuple[int, int, int, int] = (0, 0, 0, 0),
    usbc_4: tuple[int, int, int, int] = (0, 0, 0, 0),
    usba_1: tuple[int, int, int, int] = (0, 0, 0, 0),
    usba_2: tuple[int, int, int, int] = (0, 0, 0, 0),
    msg_timestamp: int = 1776485920,
) -> tuple[str, dict]:
    """Realtime port consumption (0303) payload; same layout as 0a00 minus AC/sw_version."""
    # The 0303 map's port fields are a2..a7 (not a4..a9 like 0a00).
    fields = {
        "a2": (0x06, _usb_port_bytes(*usbc_1)),
        "a3": (0x06, _usb_port_bytes(*usbc_2)),
        "a4": (0x06, _usb_port_bytes(*usbc_3)),
        "a5": (0x06, _usb_port_bytes(*usbc_4)),
        "a6": (0x06, _usb_port_bytes(*usba_1)),
        "a7": (0x06, _usb_port_bytes(*usba_2)),
        "fe": (0x03, msg_timestamp.to_bytes(4, "little")),
    }
    hex_str = _build((0x03, 0x03), fields)

    expected: dict = {"msg_timestamp": msg_timestamp}
    for port_name, port_values in [
        ("usbc_1", usbc_1),
        ("usbc_2", usbc_2),
        ("usbc_3", usbc_3),
        ("usbc_4", usbc_4),
        ("usba_1", usba_1),
        ("usba_2", usba_2),
    ]:
        status, voltage_mv, current_ma, power_cw = port_values
        expected[f"{port_name}_status"] = status
        expected[f"{port_name}_voltage"] = round(voltage_mv * 0.001, 3)
        expected[f"{port_name}_current"] = round(current_ma * 0.001, 3)
        expected[f"{port_name}_power"] = round(power_cw * 0.01, 2)

    return hex_str, expected


def port_switch_ack_0302(
    *,
    port_select: int = 0,
    switch_state: int = 1,
    msg_timestamp: int = 1776485925,
) -> tuple[str, dict]:
    """Port-switch state broadcast (0302) sent by the device after a 0207 command."""
    fields = {
        "a2": (0x01, bytes([port_select])),
        "a3": (0x01, bytes([switch_state])),
        "fe": (0x03, msg_timestamp.to_bytes(4, "little")),
    }
    hex_str = _build((0x03, 0x02), fields)
    expected = {
        "set_port_switch_select": port_select,
        "set_port_switch": switch_state,
        "msg_timestamp": msg_timestamp,
    }
    return hex_str, expected
