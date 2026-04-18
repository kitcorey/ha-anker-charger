"""AnkerSolixEntity class."""

from __future__ import annotations  # noqa: I001

from dataclasses import dataclass
from enum import IntFlag

from .solixapi.apitypes import SolixSiteType
from .const import IMAGEFOLDER, DOMAIN, MANUFACTURER
from pathlib import Path
from homeassistant.helpers.entity import DeviceInfo


@dataclass(frozen=True)
class AnkerSolixPicturePath:
    """Definition of picture path for device types."""

    LOCALPATH: str = str(Path("/local"))
    IMAGEPATH: str = str(Path(LOCALPATH) / "community" / DOMAIN / IMAGEFOLDER)

    CHARGER: str = str(Path(IMAGEPATH) / "Charger_240W_A91B2_pub.png")
    A91B2: str = str(Path(IMAGEPATH) / "Charger_240W_A91B2_pub.png")


@dataclass(frozen=True)
class AnkerSolixEntityType:
    """Definition of entity types used."""

    ACCOUNT: str = "account"
    SITE: str = "site"
    DEVICE: str = "device"
    VEHICLE: str = "vehicle"


@dataclass(frozen=True)
class AnkerSolixEntityRequiredKeyMixin:
    """Sensor entity description with required extra keys."""

    json_key: str


class AnkerSolixEntityFeature(IntFlag):
    """Supported features of the Anker Solix Entities."""

    SOLARBANK_SCHEDULE = 1
    ACCOUNT_INFO = 2
    SYSTEM_INFO = 4
    AC_CHARGE = 8


def get_AnkerSolixSubdeviceInfo(
    data: dict, identifier: str, maindevice: str
) -> DeviceInfo:
    """Return an Anker Solix Sub Device DeviceInfo."""

    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        manufacturer=MANUFACTURER,
        model=data.get("name") or data.get("device_pn"),
        # Use new model_id attribute supported since core 2024.8.0
        model_id=data.get("device_pn"),
        serial_number=data.get("device_sn"),
        name=data.get("alias") or data.get("name"),
        sw_version=data.get("sw_version"),
        # map to main device
        via_device=(DOMAIN, maindevice),
    )


def get_AnkerSolixDeviceInfo(data: dict, identifier: str, account: str) -> DeviceInfo:
    """Return an Anker Solix End Device DeviceInfo."""

    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        manufacturer=MANUFACTURER,
        model=data.get("name") or data.get("device_pn"),
        # Use new model_id attribute supported since core 2024.8.0
        model_id=data.get("device_pn"),
        serial_number=data.get("device_sn"),
        name=data.get("alias") or data.get("name"),
        sw_version=data.get("sw_version"),
        # map to site, or map standalone devices to account device
        via_device=(DOMAIN, data.get("site_id") or account),
    )


def get_AnkerSolixSystemInfo(data: dict, identifier: str, account: str) -> DeviceInfo:
    """Return an Anker Solix System DeviceInfo."""

    power_site_type = data.get("power_site_type")
    site_type = getattr(SolixSiteType, "t_" + str(power_site_type), "")
    if account:
        return DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer=MANUFACTURER,
            serial_number=data.get("site_id"),
            model=(str(site_type).capitalize() + " Site").strip(),
            model_id=f"Type {data.get('power_site_type')}",
            name=f"System {data.get('site_name')}",
            via_device=(DOMAIN, account),
        )
    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        manufacturer=MANUFACTURER,
        serial_number=data.get("site_id"),
        model=(str(site_type).capitalize() + " Site").strip(),
        model_id=f"Type {data.get('power_site_type')}",
        name=f"System {data.get('site_name')}",
    )


def get_AnkerSolixAccountInfo(
    data: dict,
    identifier: str,
) -> DeviceInfo:
    """Return an Anker Solix Account DeviceInfo."""

    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        manufacturer=MANUFACTURER,
        serial_number=identifier,
        model=str(data.get("type")).capitalize(),
        model_id=data.get("server"),
        name=f"{data.get('nickname')} ({str(data.get('country') or '--').upper()})",
    )


def get_AnkerSolixVehicleInfo(data: dict, identifier: str, account: str) -> DeviceInfo:
    """Return an Anker Solix Vehicle DeviceInfo."""

    return DeviceInfo(
        identifiers={(DOMAIN, identifier)},
        manufacturer=data.get("brand"),
        serial_number=identifier,
        model=str(data.get("type")).capitalize(),
        model_id=data.get("model"),
        hw_version=data.get("productive_year"),
        name=data.get("vehicle_name"),
        via_device=(DOMAIN, account),
    )
