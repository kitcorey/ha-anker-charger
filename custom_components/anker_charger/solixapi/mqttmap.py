"""MQTT message field mapping for the Anker Prime 240W Charging Station (A91B2).

The upstream map covered every Anker Solix device family; this fork describes
only the A91B2 charger. See the comment block below for the message-field
encoding conventions carried over from upstream.
"""

from typing import Final

from .apitypes import DeviceHexDataTypes
from .mqttcmdmap import (
    BYTES,
    CMD_REALTIME_TRIGGER,
    CMD_STATUS_REQUEST,
    CMD_USB_PORT_SWITCH,
    COMMAND_LIST,
    FACTOR,
    NAME,
    STATE_NAME,
    TOPIC,
    TYPE,
    VALUE_DEFAULT,
    VALUE_OPTIONS,
    SolixMqttCommands,
)

# Encoding conventions (carried over from upstream documentation):
#   Field format 0x00 — variable-length string (base type)
#   Field format 0x01 — 1-byte unsigned int (base type); FACTOR optional
#   Field format 0x02 — 2-byte signed int LE (base type); FACTOR optional
#   Field format 0x03 — 4-byte block of 1–4 ints or 1–2 signed ints LE; "values" required
#   Field format 0x04 — bit mask pattern with per-bit MASK
#   Field format 0x05 — 4-byte signed float LE
#   Field format 0x06 — mixed bytes, per-byte TYPE + LENGTH
# Names containing "timestamp" are converted to human-readable form.
# Names containing "sw_" or "version" are converted to a version string.

# ---------------------------------------------------------------------------
# Helper: parser for USB port consumption data. The A91B2 0303 realtime message
# reuses the same layout pioneered on the A2345 250W Prime Charger — six ports
# (four USB-C + two USB-A) each described by status / voltage / current / power.
# ---------------------------------------------------------------------------

_USB_PORT_CONSUMPTION = {
    TOPIC: "state_info",
    "a2": {
        BYTES: {
            "00": {NAME: "usbc_1_status", TYPE: DeviceHexDataTypes.ui.value},
            "01": {NAME: "usbc_1_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "03": {NAME: "usbc_1_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "05": {NAME: "usbc_1_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
        }
    },
    "a3": {
        BYTES: {
            "00": {NAME: "usbc_2_status", TYPE: DeviceHexDataTypes.ui.value},
            "01": {NAME: "usbc_2_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "03": {NAME: "usbc_2_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "05": {NAME: "usbc_2_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
        }
    },
    "a4": {
        BYTES: {
            "00": {NAME: "usbc_3_status", TYPE: DeviceHexDataTypes.ui.value},
            "01": {NAME: "usbc_3_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "03": {NAME: "usbc_3_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "05": {NAME: "usbc_3_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
        }
    },
    "a5": {
        BYTES: {
            "00": {NAME: "usbc_4_status", TYPE: DeviceHexDataTypes.ui.value},
            "01": {NAME: "usbc_4_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "03": {NAME: "usbc_4_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "05": {NAME: "usbc_4_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
        }
    },
    "a6": {
        BYTES: {
            "00": {NAME: "usba_1_status", TYPE: DeviceHexDataTypes.ui.value},
            "01": {NAME: "usba_1_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "03": {NAME: "usba_1_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "05": {NAME: "usba_1_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
        }
    },
    "a7": {
        BYTES: {
            "00": {NAME: "usba_2_status", TYPE: DeviceHexDataTypes.ui.value},
            "01": {NAME: "usba_2_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "03": {NAME: "usba_2_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
            "05": {NAME: "usba_2_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
        }
    },
    "fe": {NAME: "msg_timestamp"},
}


SOLIXMQTTMAP: Final[dict] = {
    "A91B2": {
        "0200": CMD_STATUS_REQUEST,  # status request triggering an 0a00 reply
        "0207": {
            # AC outlet switch command. Same message type as the A2345 USB port
            # switch. port_select: 0=ac_1, 1=ac_2.
            COMMAND_LIST: [
                SolixMqttCommands.ac_1_port_switch,
                SolixMqttCommands.ac_2_port_switch,
            ],
            SolixMqttCommands.ac_1_port_switch: CMD_USB_PORT_SWITCH
            | {
                "a2": {
                    **CMD_USB_PORT_SWITCH["a2"],
                    VALUE_DEFAULT: 0,
                    VALUE_OPTIONS: {
                        "ac_1_switch": 0,
                        "ac_2_switch": 1,
                    },
                },
                "a3": {
                    **CMD_USB_PORT_SWITCH["a3"],
                    STATE_NAME: "ac_1_switch",
                },
            },
            SolixMqttCommands.ac_2_port_switch: CMD_USB_PORT_SWITCH
            | {
                "a2": {
                    **CMD_USB_PORT_SWITCH["a2"],
                    VALUE_DEFAULT: 1,
                    VALUE_OPTIONS: {
                        "ac_1_switch": 0,
                        "ac_2_switch": 1,
                    },
                },
                "a3": {
                    **CMD_USB_PORT_SWITCH["a3"],
                    STATE_NAME: "ac_2_switch",
                },
            },
        },
        # Realtime trigger (no a2/a3 timeout params).
        "020b": {
            k: v for k, v in CMD_REALTIME_TRIGGER.items() if k not in ["a2", "a3"]
        },
        # Port switch state broadcast after a 0207 command.
        "0302": {
            "a2": {NAME: "set_port_switch_select"},
            "a3": {NAME: "set_port_switch"},
            "fe": {NAME: "msg_timestamp"},
        },
        # USB port consumption, ~1 s interval with realtime trigger active.
        "0303": _USB_PORT_CONSUMPTION,
        # Full device status including AC outlet switch states, sent on status request.
        "0a00": {
            "a2": {NAME: "sw_version", "values": 3},
            # USB ports at a4..a9 share the same 8-byte structure as 0303's a2..a7.
            "a4": {
                BYTES: {
                    "00": {NAME: "usbc_1_status", TYPE: DeviceHexDataTypes.ui.value},
                    "01": {NAME: "usbc_1_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "03": {NAME: "usbc_1_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "05": {NAME: "usbc_1_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
                }
            },
            "a5": {
                BYTES: {
                    "00": {NAME: "usbc_2_status", TYPE: DeviceHexDataTypes.ui.value},
                    "01": {NAME: "usbc_2_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "03": {NAME: "usbc_2_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "05": {NAME: "usbc_2_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
                }
            },
            "a6": {
                BYTES: {
                    "00": {NAME: "usbc_3_status", TYPE: DeviceHexDataTypes.ui.value},
                    "01": {NAME: "usbc_3_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "03": {NAME: "usbc_3_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "05": {NAME: "usbc_3_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
                }
            },
            "a7": {
                BYTES: {
                    "00": {NAME: "usbc_4_status", TYPE: DeviceHexDataTypes.ui.value},
                    "01": {NAME: "usbc_4_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "03": {NAME: "usbc_4_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "05": {NAME: "usbc_4_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
                }
            },
            "a8": {
                BYTES: {
                    "00": {NAME: "usba_1_status", TYPE: DeviceHexDataTypes.ui.value},
                    "01": {NAME: "usba_1_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "03": {NAME: "usba_1_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "05": {NAME: "usba_1_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
                }
            },
            "a9": {
                BYTES: {
                    "00": {NAME: "usba_2_status", TYPE: DeviceHexDataTypes.ui.value},
                    "01": {NAME: "usba_2_voltage", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "03": {NAME: "usba_2_current", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.001},
                    "05": {NAME: "usba_2_power", TYPE: DeviceHexDataTypes.sile.value, FACTOR: 0.01},
                }
            },
            # AC outlet switch states: byte "00" of f_value = 0 (off) or 1 (on).
            "aa": {
                BYTES: {
                    "00": {NAME: "ac_1_switch", TYPE: DeviceHexDataTypes.ui.value},
                },
            },
            "ab": {
                BYTES: {
                    "00": {NAME: "ac_2_switch", TYPE: DeviceHexDataTypes.ui.value},
                },
            },
            "fe": {NAME: "msg_timestamp"},
        },
    },
}
