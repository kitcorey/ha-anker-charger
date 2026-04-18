"""Anker Solix API entry point, narrowed to A91B2 charger support.

Original upstream class bundled Solarbank/HES/PowerPanel/PPS/Vehicle logic
from sibling modules (energy.py, schedule.py, vehicle.py, poller.py,
powerpanel.py, hesapi.py, export.py). All of that is gone in this fork.
The A91B2 charger only needs:
  * cloud login (inherited from AnkerSolixBaseApi)
  * get_bind_devices to populate the charger cache
  * MQTT session for real-time port data (inherited)
"""

from __future__ import annotations

import contextlib
import logging

from aiohttp import ClientSession

from .apibase import AnkerSolixBaseApi
from .apitypes import SolixDeviceCategory
from .session import AnkerSolixClientSession

_LOGGER: logging.Logger = logging.getLogger(__name__)

# wifi_signal percentage is derived from rssi dBm clamped to this range.
_RSSI_DBM_MAX = -50  # strong signal → 100%
_RSSI_DBM_MIN = -85  # no connection → 0%


class AnkerSolixApi(AnkerSolixBaseApi):
    """Minimal API client for A91B2 chargers."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        countryId: str | None = None,
        websession: ClientSession | None = None,
        logger: logging.Logger | None = None,
        apisession: AnkerSolixClientSession | None = None,
    ) -> None:
        """Initialize the charger-specific API instance."""
        super().__init__(
            email=email,
            password=password,
            countryId=countryId,
            websession=websession,
            logger=logger,
            apisession=apisession,
        )
        # Backward-compatible attribute aliases used by api_client / coordinator.
        self.request_count = self.apisession.request_count
        self.async_authenticate = self.apisession.async_authenticate

    def _update_dev(
        self,
        devData: dict,
        devType: str | None = None,
        siteId: str | None = None,
        isAdmin: bool | None = None,
    ) -> str | None:
        """Normalize cloud field names and set charger-specific metadata.

        The cloud bind_devices response uses `product_code`, `device_name`,
        `alias_name`, and `device_sw_version`. The rest of the integration
        expects `device_pn`, `name`, `alias`, `sw_version`. Rename first,
        then hand off to the base class for the common merge logic.
        """
        if not devData.get("device_sn"):
            return None

        normalized = dict(devData)
        if (product_code := normalized.pop("product_code", None)) is not None:
            normalized["device_pn"] = str(product_code)
        if (device_name := normalized.pop("device_name", None)) is not None:
            normalized["name"] = str(device_name)
        if (alias_name := normalized.pop("alias_name", None)) is not None:
            normalized["alias"] = str(alias_name)

        # Infer the device type from the part number when the caller did not pass one.
        if devType is None and (pn := normalized.get("device_pn")) is not None:
            category = getattr(SolixDeviceCategory, str(pn), None)
            if category:
                devType = str(category).split("_")[0]

        sn = super()._update_dev(
            normalized,
            devType=devType,
            siteId=siteId,
            isAdmin=isAdmin,
        )
        if not sn:
            return None

        device = self.devices[sn]

        # Mark admin chargers as MQTT-capable so the MQTT session subscribes to them.
        if (device.get("is_admin") or device.get("owner_user_id")) and not device.get(
            "is_passive"
        ):
            device["mqtt_supported"] = True
            device.setdefault("mqtt_overlay", False)
            device.setdefault("mqtt_status_request", True)

        # Derive a wifi_signal percentage from the rssi dBm value reported by the cloud,
        # unless a percentage was provided directly.
        if "wifi_signal" not in device and (rssi := device.get("rssi")) is not None:
            with contextlib.suppress(ValueError, TypeError):
                rssi_value = float(rssi)
                if rssi_value:
                    pct = max(
                        0,
                        min(
                            100,
                            (rssi_value - _RSSI_DBM_MIN)
                            * 100
                            / (_RSSI_DBM_MAX - _RSSI_DBM_MIN),
                        ),
                    )
                    device["wifi_signal"] = str(round(pct))

        return sn

    async def update_sites(
        self,
        siteId: str | None = None,
        fromFile: bool = False,
        exclude: set | None = None,
    ) -> dict:
        """A91B2 is a standalone charger; there are no sites to populate."""
        return {}

    async def update_site_details(
        self, fromFile: bool = False, exclude: set | None = None
    ) -> dict:
        """No site details for a standalone charger."""
        return {}

    async def update_device_details(
        self, fromFile: bool = False, exclude: set | None = None
    ) -> dict:
        """Refresh the device cache via the cloud bind_devices endpoint."""
        self._update_account({"use_files": fromFile})
        await self.get_bind_devices(fromFile=fromFile)
        return self.devices

    async def update_device_energy(
        self, fromFile: bool = False, exclude: set | None = None
    ) -> dict:
        """No energy statistics exposed for the A91B2 charger."""
        return {}

    async def get_vehicle_list(self, fromFile: bool = False) -> dict:
        """Vehicles are not supported in this fork."""
        return {}

    async def get_vehicle_details(
        self, vehicleId: str | None = None, fromFile: bool = False
    ) -> dict:
        """Vehicles are not supported in this fork."""
        return {}

    async def manage_vehicle(
        self,
        vehicleId: str | None = None,
        action: str | None = None,
        toFile: bool = False,
    ) -> dict | None:
        """Vehicles are not supported in this fork."""
        return None
