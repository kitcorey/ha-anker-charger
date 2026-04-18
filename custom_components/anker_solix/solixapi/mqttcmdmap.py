"""MQTT command descriptors for the Anker Prime 240W Charging Station (A91B2).

Originally this module catalogued the entire Anker Solix MQTT command surface
(Solarbank, PPS, EV charger, SmartPlug, etc.). This fork trims it to the
command patterns actually referenced by the A91B2 message map.
"""

from dataclasses import asdict, dataclass
from typing import Final

from .apitypes import DeviceHexDataTypes


# ---------------------------------------------------------------------------
# Field-descriptor key names. Used as dict keys throughout the MQTT map. Keep
# the full set here so the framework (command encoder, message decoder) has
# all the shapes it may read, even if the A91B2 map doesn't exercise them.
# ---------------------------------------------------------------------------

NAME: Final[str] = "name"
TYPE: Final[str] = "type"
TOPIC: Final[str] = "topic"
FACTOR: Final[str] = "factor"
SIGNED: Final[str] = "signed"
BYTES: Final[str] = "bytes"
LENGTH: Final[str] = "length"
MASK: Final[str] = "mask"
COMMAND_NAME: Final[str] = "command_name"
COMMAND_LIST: Final[str] = "command_list"
COMMAND_ENCODING: Final[str] = "command_encoding"
STATE_NAME: Final[str] = "state_name"
STATE_CONVERTER: Final[str] = "state_converter"
VALUE_MIN: Final[str] = "value_min"
VALUE_MAX: Final[str] = "value_max"
VALUE_STEP: Final[str] = "value_step"
VALUE_OPTIONS: Final[str] = "value_options"
VALUE_DEFAULT: Final[str] = "value_default"
VALUE_FOLLOWS: Final[str] = "value_follows"
VALUE_STATE: Final[str] = "value_state"
VALUE_MIN_STATE: Final[str] = "value_min_state"
VALUE_MAX_STATE: Final[str] = "value_max_state"
VALUE_DIVIDER: Final[str] = "value_divider"


# ---------------------------------------------------------------------------
# Command names consumed by mqtt_charger.FEATURES and mqttmap.SOLIXMQTTMAP.
# ---------------------------------------------------------------------------

@dataclass
class SolixMqttCommands:
    """Named MQTT commands supported for A91B2 charging stations."""

    status_request: str = "status_request"
    realtime_trigger: str = "realtime_trigger"
    display_switch: str = "display_switch"
    port_memory_switch: str = "port_memory_switch"
    usbc_1_port_switch: str = "usbc_1_port_switch"
    usbc_2_port_switch: str = "usbc_2_port_switch"
    usbc_3_port_switch: str = "usbc_3_port_switch"
    usbc_4_port_switch: str = "usbc_4_port_switch"
    usba_port_switch: str = "usba_port_switch"
    ac_1_port_switch: str = "ac_1_port_switch"
    ac_2_port_switch: str = "ac_2_port_switch"

    def asdict(self) -> dict:
        """Return a dictionary representation of the class fields."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Reusable command-message fragments.
# ---------------------------------------------------------------------------

TIMESTAMP_FE = {
    # 4-byte timestamp (seconds), composed automatically by the encoder.
    "fe": {
        NAME: "msg_timestamp",
        TYPE: DeviceHexDataTypes.var.value,
    },
}

CMD_HEADER = {
    # Common header shared by every command message.
    TOPIC: "req",
    "a1": {NAME: "pattern_22"},
}

CMD_COMMON = CMD_HEADER | TIMESTAMP_FE


# ---------------------------------------------------------------------------
# Command patterns referenced by the A91B2 SOLIXMQTTMAP entry.
# ---------------------------------------------------------------------------

CMD_STATUS_REQUEST = CMD_COMMON | {
    # Ask the device for a full 0a00 status reply.
    COMMAND_NAME: SolixMqttCommands.status_request,
}

CMD_REALTIME_TRIGGER = CMD_COMMON | {
    # Enable or disable streaming of 0303 realtime messages.
    COMMAND_NAME: SolixMqttCommands.realtime_trigger,
    "a2": {
        NAME: "set_realtime_trigger",  # 0 = off, 1 = on
        TYPE: DeviceHexDataTypes.ui.value,
        VALUE_OPTIONS: {"off": 0, "on": 1},
        VALUE_DEFAULT: 1,
    },
    "a3": {
        NAME: "trigger_timeout_sec",  # seconds the stream stays active
        TYPE: DeviceHexDataTypes.var.value,
        VALUE_MIN: 60,
        VALUE_MAX: 600,
        VALUE_DEFAULT: 60,
    },
}

CMD_USB_PORT_SWITCH = CMD_COMMON | {
    # Toggle one of the USB-C / USB-A / AC outlet ports on the charging station.
    # COMMAND_NAME must be filled in per-port by the model entry.
    "a2": {
        NAME: "set_port_switch_select",
        TYPE: DeviceHexDataTypes.ui.value,
        VALUE_OPTIONS: {
            "usbc_1_switch": 0,
            "usbc_2_switch": 1,
            "usbc_3_switch": 2,
            "usbc_4_switch": 3,
            "usba_switch": 4,
        },
    },
    "a3": {
        NAME: "set_port_switch",
        TYPE: DeviceHexDataTypes.ui.value,
        VALUE_OPTIONS: {"off": 0, "on": 1},
    },
}
