"""MQTT device control for the Anker Prime 240W Charging Station (A91B2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .mqtt_device import SolixMqttDevice
from .mqttcmdmap import SolixMqttCommands

if TYPE_CHECKING:
    from .api import AnkerSolixApi

# Supported models for this class.
MODELS = {
    "A91B2",  # Anker Prime 8-in-1 240W Charging Station
}

# Commands surfaced by this device class. Each command must also be described
# in the SOLIXMQTTMAP entry for the model to actually be usable; commands here
# without a mapping entry are silently ignored.
FEATURES = {
    SolixMqttCommands.status_request: MODELS,
    SolixMqttCommands.realtime_trigger: MODELS,
    SolixMqttCommands.usbc_1_port_switch: MODELS,
    SolixMqttCommands.usbc_2_port_switch: MODELS,
    SolixMqttCommands.usbc_3_port_switch: MODELS,
    SolixMqttCommands.usbc_4_port_switch: MODELS,
    SolixMqttCommands.usba_port_switch: MODELS,
    SolixMqttCommands.ac_1_port_switch: MODELS,
    SolixMqttCommands.ac_2_port_switch: MODELS,
    SolixMqttCommands.display_switch: MODELS,
    SolixMqttCommands.port_memory_switch: MODELS,
}


class SolixMqttDeviceCharger(SolixMqttDevice):
    """MQTT control wrapper for an A91B2 charging station."""

    def __init__(self, api_instance: AnkerSolixApi, device_sn: str) -> None:
        self.models = MODELS
        self.features = FEATURES
        super().__init__(api_instance=api_instance, device_sn=device_sn)
