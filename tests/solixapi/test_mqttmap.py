"""Structural tests for ``SOLIXMQTTMAP["A91B2"]``.

These tests lock the shape of the A91B2 message map so accidental edits
(renamed fields, swapped offsets, missing message types) fail fast.
"""

from __future__ import annotations

from custom_components.anker_charger.solixapi.mqttcmdmap import (
    COMMAND_LIST,
    COMMAND_NAME,
    SolixMqttCommands,
)
from custom_components.anker_charger.solixapi.mqttmap import SOLIXMQTTMAP


def _a91b2() -> dict:
    return SOLIXMQTTMAP["A91B2"]


def test_map_contains_only_a91b2():
    assert set(SOLIXMQTTMAP) == {"A91B2"}


def test_all_required_message_types_present():
    assert set(_a91b2()) == {"0200", "0207", "020b", "0302", "0303", "0a00"}


def test_status_request_command_name():
    cmd = _a91b2()["0200"]
    assert cmd[COMMAND_NAME] == SolixMqttCommands.status_request


def test_realtime_trigger_command_name():
    cmd = _a91b2()["020b"]
    assert cmd[COMMAND_NAME] == SolixMqttCommands.realtime_trigger


def test_port_switch_map_contains_both_ac_outlets():
    cmd = _a91b2()["0207"]
    assert cmd[COMMAND_LIST] == [
        SolixMqttCommands.ac_1_port_switch,
        SolixMqttCommands.ac_2_port_switch,
    ]
    assert SolixMqttCommands.ac_1_port_switch in cmd
    assert SolixMqttCommands.ac_2_port_switch in cmd


def test_0a00_has_six_usb_port_blocks():
    msg = _a91b2()["0a00"]
    # USB ports live at a4..a9 per the comments in mqttmap.
    for offset in ("a4", "a5", "a6", "a7", "a8", "a9"):
        assert offset in msg, f"missing USB port block {offset}"


def test_0a00_has_both_ac_outlet_switches():
    msg = _a91b2()["0a00"]
    assert "aa" in msg
    assert "ab" in msg


def test_0a00_usb_port_field_names_follow_pattern():
    """a4 → usbc_1_*, a5 → usbc_2_*, …, a8 → usba_1_*, a9 → usba_2_*."""
    msg = _a91b2()["0a00"]
    expected_prefixes = {
        "a4": "usbc_1",
        "a5": "usbc_2",
        "a6": "usbc_3",
        "a7": "usbc_4",
        "a8": "usba_1",
        "a9": "usba_2",
    }
    for offset, prefix in expected_prefixes.items():
        block = msg[offset]["bytes"]
        names = {entry["name"] for entry in block.values()}
        assert names == {
            f"{prefix}_status",
            f"{prefix}_voltage",
            f"{prefix}_current",
            f"{prefix}_power",
        }


def test_0303_has_six_usb_port_blocks_at_a2_a7():
    """0303 realtime frames use a2..a7 for USB ports (offset by two vs 0a00)."""
    msg = _a91b2()["0303"]
    for offset in ("a2", "a3", "a4", "a5", "a6", "a7"):
        assert offset in msg


def test_0302_port_switch_ack_fields():
    msg = _a91b2()["0302"]
    assert msg["a2"]["name"] == "set_port_switch_select"
    assert msg["a3"]["name"] == "set_port_switch"


def test_every_msgtype_has_timestamp():
    """Every incoming message (not commands) publishes ``fe`` timestamp."""
    for msgtype in ("0302", "0303", "0a00"):
        assert "fe" in _a91b2()[msgtype]
