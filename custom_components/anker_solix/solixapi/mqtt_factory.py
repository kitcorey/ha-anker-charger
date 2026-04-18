"""Device factory for creating MQTT device control instances.

Narrowed to A91B2 charger support: the upstream factory also dispatched to
Solarbank, PPS, and SmartPlug device classes, which have been removed from
this fork.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .apitypes import SolixDeviceType
from .mqtt_charger import MODELS as CHARGER_MODELS, SolixMqttDeviceCharger
from .mqtt_device import SolixMqttDevice
from .mqttmap import SOLIXMQTTMAP

if TYPE_CHECKING:
    from .api import AnkerSolixApi


class SolixMqttDeviceFactory:
    """Create the appropriate MQTT device object for a device serial."""

    def __init__(self, api_instance: AnkerSolixApi, device_sn: str) -> None:
        self.api = api_instance
        self.device_sn = device_sn
        self.device_data = getattr(api_instance, "devices", {}).get(device_sn) or {}

    def create_device(self) -> SolixMqttDevice | None:
        """Return the MQTT device instance for the serial, or None if unknown."""
        if not (category := (self.device_data or {}).get("type")):
            return None
        pn = self.device_data.get("device_pn") or ""
        if pn not in SOLIXMQTTMAP:
            return None
        if category == SolixDeviceType.CHARGER.value and pn in CHARGER_MODELS:
            return SolixMqttDeviceCharger(self.api, self.device_sn)
        # Fallback: plain MQTT device supporting only the realtime trigger.
        return SolixMqttDevice(self.api, self.device_sn)
