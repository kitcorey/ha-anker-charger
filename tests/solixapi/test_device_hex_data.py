"""Decoder tests for ``DeviceHexData`` against synthetic A91B2 payloads.

These round-trip hex strings through ``DeviceHexData`` + the A91B2
SOLIXMQTTMAP entry and assert the extracted values match the known inputs.
The payloads themselves are assembled in ``tests/fixtures/mqtt_payloads.py``
and cover the four message types the A91B2 map supports today: 0a00 (full
status), 0303 (realtime port consumption), 0302 (port switch state
broadcast), plus decoder edge cases.
"""

from __future__ import annotations

import pytest

from custom_components.anker_charger.solixapi.mqttmap import SOLIXMQTTMAP
from custom_components.anker_charger.solixapi.mqtttypes import DeviceHexData
from tests.fixtures import mqtt_payloads


def _decode_all(hex_str: str) -> dict:
    """Parse ``hex_str`` and extract every field through the A91B2 fieldmap."""
    hd = DeviceHexData(hexbytes=hex_str, model="A91B2")
    fmap = hd._get_fieldmap()
    values: dict = {}
    for field in hd.msg_fields.values():
        key = field.f_name.hex()
        if key in fmap:
            values.update(
                field.extract_value(
                    hexdata=field.f_value,
                    fieldtype=field.f_type,
                    fieldmap=fmap[key],
                )
            )
    return values


class TestStatus0a00:
    """0a00 is the device's full status reply to a status_request command."""

    def test_all_ports_off(self):
        hex_str, expected = mqtt_payloads.status_0a00()
        assert _decode_all(hex_str) == expected

    def test_usbc_1_charging(self):
        hex_str, expected = mqtt_payloads.status_0a00(
            # status=1 (active), 5.123 V, 1.5 A, 7.68 W
            usbc_1=(1, 5123, 1500, 768),
        )
        decoded = _decode_all(hex_str)
        assert decoded["usbc_1_status"] == 1
        assert decoded["usbc_1_voltage"] == pytest.approx(5.123)
        assert decoded["usbc_1_current"] == pytest.approx(1.5)
        assert decoded["usbc_1_power"] == pytest.approx(7.68)
        # Other ports remain zero.
        assert decoded["usbc_2_power"] == 0
        assert decoded["usba_1_power"] == 0

    def test_ac_outlets_on(self):
        hex_str, _ = mqtt_payloads.status_0a00(
            ac_1_switch=1,
            ac_2_switch=1,
        )
        decoded = _decode_all(hex_str)
        assert decoded["ac_1_switch"] == 1
        assert decoded["ac_2_switch"] == 1

    def test_mixed_ports(self):
        # USB-C 4 charging at high draw, USB-A 1 trickle, AC outlet 1 on.
        hex_str, _ = mqtt_payloads.status_0a00(
            usbc_4=(1, 20000, 5000, 10000),
            usba_1=(1, 5000, 50, 25),
            ac_1_switch=1,
        )
        decoded = _decode_all(hex_str)
        assert decoded["usbc_4_status"] == 1
        assert decoded["usbc_4_voltage"] == pytest.approx(20.0)
        assert decoded["usbc_4_current"] == pytest.approx(5.0)
        assert decoded["usbc_4_power"] == pytest.approx(100.0)
        assert decoded["usba_1_status"] == 1
        assert decoded["usba_1_power"] == pytest.approx(0.25)
        assert decoded["ac_1_switch"] == 1
        assert decoded["ac_2_switch"] == 0

    def test_timestamp_extracted(self):
        hex_str, _ = mqtt_payloads.status_0a00(msg_timestamp=1776485917)
        assert _decode_all(hex_str)["msg_timestamp"] == 1776485917

    def test_signed_current(self):
        # The sile type is signed LE — a discharging port would emit a
        # negative current value. Assert the sign is preserved.
        hex_str, _ = mqtt_payloads.status_0a00(usbc_1=(1, 5000, -500, 250))
        decoded = _decode_all(hex_str)
        assert decoded["usbc_1_current"] == pytest.approx(-0.5)


class TestRealtime0303:
    """0303 is the ~1 Hz stream sent while a realtime_trigger is active."""

    def test_all_ports_zero(self):
        hex_str, expected = mqtt_payloads.realtime_0303()
        assert _decode_all(hex_str) == expected

    def test_multi_port_active(self):
        hex_str, _ = mqtt_payloads.realtime_0303(
            usbc_1=(1, 5100, 3000, 1530),
            usba_2=(1, 5000, 1000, 500),
        )
        decoded = _decode_all(hex_str)
        assert decoded["usbc_1_power"] == pytest.approx(15.3)
        assert decoded["usba_2_power"] == pytest.approx(5.0)
        # No AC or sw_version fields in 0303 realtime frames.
        assert "ac_1_switch" not in decoded
        assert "sw_version" not in decoded


class TestPortSwitchAck0302:
    """0302 is broadcast by the device after a 0207 port-switch command."""

    def test_switch_on(self):
        hex_str, expected = mqtt_payloads.port_switch_ack_0302(
            port_select=1,
            switch_state=1,
        )
        assert _decode_all(hex_str) == expected

    def test_switch_off(self):
        hex_str, expected = mqtt_payloads.port_switch_ack_0302(
            port_select=0,
            switch_state=0,
        )
        decoded = _decode_all(hex_str)
        assert decoded["set_port_switch_select"] == 0
        assert decoded["set_port_switch"] == 0


class TestHeaderParsing:
    """Validate the header parser against known layouts."""

    def test_prefix_and_msgtype_extracted(self):
        hex_str, _ = mqtt_payloads.status_0a00()
        hd = DeviceHexData(hexbytes=hex_str, model="A91B2")
        assert hd.msg_header.prefix.hex() == "ff09"
        assert hd.msg_header.msgtype.hex() == "0a00"
        assert hd.msg_header.pattern.hex() == "03010f"

    def test_length_matches_bytes(self):
        """The parsed length should equal the raw byte count of the payload."""
        hex_str, _ = mqtt_payloads.realtime_0303()
        hd = DeviceHexData(hexbytes=hex_str, model="A91B2")
        assert hd.length == len(bytes.fromhex(hex_str))

    def test_checksum_byte_is_xor_of_message(self):
        hex_str, _ = mqtt_payloads.status_0a00()
        hd = DeviceHexData(hexbytes=hex_str, model="A91B2")
        # XOR of every byte except the trailing checksum should equal the checksum.
        body = hd.hexbytes[:-1]
        xor = 0
        for b in body:
            xor ^= b
        assert bytes(hd.checksum) == bytes([xor])


class TestUnknownMessageType:
    """Unmapped msgtypes return an empty fieldmap — no crash, no values."""

    def test_fieldmap_empty_for_unknown_msgtype(self):
        # Construct a message with a msgtype we don't have in SOLIXMQTTMAP.
        hex_str, _ = mqtt_payloads.port_switch_ack_0302()
        hd = DeviceHexData(hexbytes=hex_str, model="A91B2")
        hd.msg_header.msgtype = bytearray([0xFF, 0xFF])
        assert hd._get_fieldmap() == {}

    def test_empty_model_returns_empty_map(self):
        hex_str, _ = mqtt_payloads.status_0a00()
        hd = DeviceHexData(hexbytes=hex_str, model="NOTAPN")
        assert hd._get_fieldmap() == {}


def test_a91b2_map_is_the_only_model():
    """Guard: SOLIXMQTTMAP should only contain A91B2 in this fork."""
    assert set(SOLIXMQTTMAP.keys()) == {"A91B2"}
