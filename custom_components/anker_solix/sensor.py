"""Sensor platform for anker_solix."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from random import choice, randrange
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_EXCLUDE,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .config_flow import _SCAN_INTERVAL_MIN
from .const import (
    ALLOW_EXPORT,
    ALLOW_TESTMODE,
    APPLIANCE_LOAD,
    ATTRIBUTION,
    CHARGE_PRIORITY_LIMIT,
    CONF_SKIP_INVALID,
    CREATE_ALL_ENTITIES,
    DEVICE_LOAD,
    DISCHARGE_PRIORITY,
    DOMAIN,
    END_TIME,
    INCLUDE_CACHE,
    LOGGER,
    MQTT_OVERLAY,
    PLAN,
    SERVICE_CLEAR_SOLARBANK_SCHEDULE,
    SERVICE_GET_SOLARBANK_SCHEDULE,
    SERVICE_GET_SYSTEM_INFO,
    SERVICE_SET_SOLARBANK_SCHEDULE,
    SERVICE_UPDATE_SOLARBANK_SCHEDULE,
    SOLARBANK_TIMESLOT_SCHEMA,
    SOLIX_ENTITY_SCHEMA,
    SOLIX_WEEKDAY_SCHEMA,
    START_TIME,
    TEST_NUMBERVARIANCE,
    WEEK_DAYS,
)
from .coordinator import AnkerSolixDataUpdateCoordinator
from .entity import (
    AnkerSolixEntityFeature,
    AnkerSolixEntityRequiredKeyMixin,
    AnkerSolixEntityType,
    AnkerSolixPicturePath,
    get_AnkerSolixAccountInfo,
    get_AnkerSolixDeviceInfo,
    get_AnkerSolixSubdeviceInfo,
    get_AnkerSolixSystemInfo,
    get_AnkerSolixVehicleInfo,
)
from .solixapi.apitypes import (
    ApiCategories,
    SmartmeterStatus,
    Solarbank2Timeslot,
    SolarbankAiemsRuntimeStatus,
    SolarbankPowerMode,
    SolarbankPpsStatus,
    SolarbankRatePlan,
    SolarbankStatus,
    SolarbankTimeslot,
    SolarbankUsageMode,
    SolixBatteryStatus,
    SolixChargerPortStatus,
    SolixDeviceStatus,
    SolixDeviceType,
    SolixGridStatus,
    SolixParmType,
    SolixPlantStatus,
    SolixPpsDcChargingStatus,
    SolixPpsPortStatus,
    SolixRoleStatus,
    SolixWorkingStatus,
)
from .solixapi.helpers import get_enum_name


@dataclass(frozen=True)
class AnkerSolixSensorDescription(
    SensorEntityDescription, AnkerSolixEntityRequiredKeyMixin
):
    """Sensor entity description with optional keys."""

    picture_path: str = None
    feature: AnkerSolixEntityFeature | None = None
    check_invalid: bool = False
    restore: bool = False
    mqtt: bool = False
    # Use optionally to provide function for value calculation or lookup of nested values
    value_fn: Callable[[dict, str, str], StateType] = lambda d, jk, ctx: d.get(jk)
    attrib_fn: Callable[[dict, str], dict | None] = lambda d, ctx: None
    unit_fn: Callable[[dict, str], dict | None] = lambda d, ctx: None
    force_creation_fn: Callable[[dict], bool] = lambda d: False
    exclude_fn: Callable[[set, dict], bool] = lambda s, d: False
    nested_sensor: bool = False


DEVICE_SENSORS = [
    AnkerSolixSensorDescription(
        # Firmware version from cloud bind_devices payload; registers charger
        # device in HA registry even when MQTT is disabled or not yet connected.
        key="sw_version",
        translation_key="sw_version",
        json_key="sw_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
    ),
    AnkerSolixSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        json_key="wifi_signal",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attrib_fn=lambda d, _: {"rssi": d.get("rssi")},
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
    ),
    AnkerSolixSensorDescription(
        # timestamp of last MQTT message with any update
        key="mqtt_timestamp",
        translation_key="mqtt_timestamp",
        json_key="last_update",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d, jk, _: (
            None
            if not (val := d.get(jk) or "")
            else (datetime.strptime(val, "%Y-%m-%d %H:%M:%S")).isoformat(sep=" ")
        ),
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        mqtt=True,
    ),
    AnkerSolixSensorDescription(
        key="usbc_1_power",
        translation_key="usbc_1_power",
        json_key="usbc_1_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attrib_fn=lambda d, _: (
            (
                {
                    "port_status": get_enum_name(
                        SolixChargerPortStatus
                        if d.get("type") == SolixDeviceType.CHARGER.value
                        else SolixPpsPortStatus,
                        str(d.get("usbc_1_status")),
                        default=SolixPpsPortStatus.unknown.name,
                    ),
                }
                if "usbc_1_status" in d
                else {}
            )
            | (
                {
                    "voltage": val,
                }
                if (val := d.get("usbc_1_voltage"))
                else {}
            )
            | (
                {
                    "current": val,
                }
                if (val := d.get("usbc_1_current"))
                else {}
            )
        ),
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        mqtt=True,
    ),
    AnkerSolixSensorDescription(
        key="usbc_2_power",
        translation_key="usbc_2_power",
        json_key="usbc_2_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attrib_fn=lambda d, _: (
            (
                {
                    "port_status": get_enum_name(
                        SolixChargerPortStatus
                        if d.get("type") == SolixDeviceType.CHARGER.value
                        else SolixPpsPortStatus,
                        str(d.get("usbc_2_status")),
                        default=SolixPpsPortStatus.unknown.name,
                    ),
                }
                if "usbc_2_status" in d
                else {}
            )
            | (
                {
                    "voltage": val,
                }
                if (val := d.get("usbc_2_voltage"))
                else {}
            )
            | (
                {
                    "current": val,
                }
                if (val := d.get("usbc_2_current"))
                else {}
            )
        ),
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        mqtt=True,
    ),
    AnkerSolixSensorDescription(
        key="usbc_3_power",
        translation_key="usbc_3_power",
        json_key="usbc_3_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attrib_fn=lambda d, _: (
            (
                {
                    "port_status": get_enum_name(
                        SolixChargerPortStatus
                        if d.get("type") == SolixDeviceType.CHARGER.value
                        else SolixPpsPortStatus,
                        str(d.get("usbc_3_status")),
                        default=SolixPpsPortStatus.unknown.name,
                    ),
                }
                if "usbc_3_status" in d
                else {}
            )
            | (
                {
                    "voltage": val,
                }
                if (val := d.get("usbc_3_voltage"))
                else {}
            )
            | (
                {
                    "current": val,
                }
                if (val := d.get("usbc_3_current"))
                else {}
            )
        ),
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        mqtt=True,
    ),
    AnkerSolixSensorDescription(
        key="usbc_4_power",
        translation_key="usbc_4_power",
        json_key="usbc_4_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attrib_fn=lambda d, _: (
            (
                {
                    "port_status": get_enum_name(
                        SolixChargerPortStatus
                        if d.get("type") == SolixDeviceType.CHARGER.value
                        else SolixPpsPortStatus,
                        str(d.get("usbc_4_status")),
                        default=SolixPpsPortStatus.unknown.name,
                    ),
                }
                if "usbc_4_status" in d
                else {}
            )
            | (
                {
                    "voltage": val,
                }
                if (val := d.get("usbc_4_voltage"))
                else {}
            )
            | (
                {
                    "current": val,
                }
                if (val := d.get("usbc_4_current"))
                else {}
            )
        ),
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        mqtt=True,
    ),
    AnkerSolixSensorDescription(
        key="usba_1_power",
        translation_key="usba_1_power",
        json_key="usba_1_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attrib_fn=lambda d, _: (
            (
                {
                    "port_status": get_enum_name(
                        SolixChargerPortStatus
                        if d.get("type") == SolixDeviceType.CHARGER.value
                        else SolixPpsPortStatus,
                        str(d.get("usba_1_status")),
                        default=SolixPpsPortStatus.unknown.name,
                    ),
                }
                if "usba_1_status" in d
                else {}
            )
            | (
                {
                    "voltage": val,
                }
                if (val := d.get("usba_1_voltage"))
                else {}
            )
            | (
                {
                    "current": val,
                }
                if (val := d.get("usba_1_current"))
                else {}
            )
        ),
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        mqtt=True,
    ),
    AnkerSolixSensorDescription(
        key="usba_2_power",
        translation_key="usba_2_power",
        json_key="usba_2_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        attrib_fn=lambda d, _: (
            (
                {
                    "port_status": get_enum_name(
                        SolixChargerPortStatus
                        if d.get("type") == SolixDeviceType.CHARGER.value
                        else SolixPpsPortStatus,
                        str(d.get("usba_2_status")),
                        default=SolixPpsPortStatus.unknown.name,
                    ),
                }
                if "usba_2_status" in d
                else {}
            )
            | (
                {
                    "voltage": val,
                }
                if (val := d.get("usba_2_voltage"))
                else {}
            )
            | (
                {
                    "current": val,
                }
                if (val := d.get("usba_2_current"))
                else {}
            )
        ),
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        mqtt=True,
    ),
]

SITE_SENSORS: list[AnkerSolixSensorDescription] = []

ACCOUNT_SENSORS = [
    AnkerSolixSensorDescription(
        # MQTT statistics
        key="mqtt_statistic",
        translation_key="mqtt_statistic",
        json_key="mqtt_statistic",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d, jk, _: (d.get(jk) or {}).get("kb_hourly_received"),
        native_unit_of_measurement="kB/h",
        suggested_display_precision=3,
        attrib_fn=lambda d, _: {
            "start_time": (d.get("mqtt_statistic") or {}).get("start_time"),
            "bytes_received": (d.get("mqtt_statistic") or {}).get("bytes_received"),
            "bytes_sent": (d.get("mqtt_statistic") or {}).get("bytes_sent"),
            "messages": (d.get("mqtt_statistic") or {}).get("dev_messages"),
        },
        mqtt=True,
    ),
]

VEHICLE_SENSORS: list[AnkerSolixSensorDescription] = []

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""

    coordinator: AnkerSolixDataUpdateCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    entities = []

    if coordinator and hasattr(coordinator, "data") and coordinator.data:
        # create entity based on type of entry in coordinator data, which consolidates the api.sites, api.devices and api.account dictionaries
        # the coordinator.data dict key is either account nickname, a site_id or device_sn and used as context for the entity to lookup its data
        for context, data in coordinator.data.items():
            mdev = None
            mdata = {}
            if (data_type := data.get("type")) == SolixDeviceType.SYSTEM.value:
                # Unique key for site_id entry in data
                entity_type = AnkerSolixEntityType.SITE
                entity_list = SITE_SENSORS
            elif data_type == SolixDeviceType.ACCOUNT.value:
                # Unique key for account entry in data
                entity_type = AnkerSolixEntityType.ACCOUNT
                entity_list = ACCOUNT_SENSORS
            elif data_type == SolixDeviceType.VEHICLE.value:
                # vehicle entry in data
                entity_type = AnkerSolixEntityType.VEHICLE
                entity_list = VEHICLE_SENSORS
            else:
                # device_sn entry in data
                entity_type = AnkerSolixEntityType.DEVICE
                entity_list = DEVICE_SENSORS
                # get MQTT device combined values for creation of entities
                if mdev := coordinator.client.get_mqtt_device(sn=context):
                    mdata = mdev.get_combined_cache(
                        fromFile=coordinator.client.testmode()
                    )

            for description in entity_list:
                if description.nested_sensor:
                    # concatenate device serial and subdevice serial to context
                    sn_list = [
                        context + "_" + serial
                        for serial in (data.get(description.json_key) or {})
                    ]
                else:
                    sn_list = [context]
                # create list of sensors to create based on data and config options
                for sn in (
                    serial
                    for serial in sn_list
                    if bool(CREATE_ALL_ENTITIES)
                    or (
                        not description.exclude_fn(
                            set(entry.options.get(CONF_EXCLUDE, [])), data
                        )
                        and (
                            description.force_creation_fn(data)
                            # filter MQTT entities and provide combined or only api cache
                            # Entities that should not be created without MQTT data need to use exclude option
                            or (
                                description.mqtt
                                and description.value_fn(
                                    mdata or data, description.json_key, serial
                                )
                                is not None
                            )
                            # filter API only entities
                            or (
                                not description.mqtt
                                and description.value_fn(
                                    data, description.json_key, serial
                                )
                                is not None
                            )
                        )
                    )
                ):
                    if description.restore:
                        entity = AnkerSolixRestoreSensor(
                            coordinator, description, context, entity_type
                        )
                    else:
                        entity = AnkerSolixSensor(
                            coordinator, description, sn, entity_type
                        )
                    entities.append(entity)

    # create the entities from the list
    async_add_entities(entities)


class AnkerSolixSensor(CoordinatorEntity, SensorEntity):
    """Represents a sensor entity for Anker device and site data."""

    coordinator: AnkerSolixDataUpdateCoordinator
    entity_description: AnkerSolixSensorDescription
    entity_type: str
    _attr_has_entity_name = True
    _context_base: str = None
    _context_nested: str = None
    _last_schedule_service_value: str = None
    _unrecorded_attributes = frozenset(
        {
            "advantage",
            "avg_today",
            "avg_tomorrow",
            "branch_ct_number",
            "bt_mac",
            "bytes_received",
            "bytes_sent",
            "current",
            "device_sn",
            "device_name",
            "device_pn",
            "energy_ah",
            "expansions"
            "fittings",
            "forecast",
            "forecast_24h",
            "forecast_hourly",
            "forecast_next_hour",
            "hour_end",
            "hourly_unit",
            "inverter_info",
            "main_ct_number",
            "main_branch_check_status",
            "message",
            "messages",
            "mode",
            "mode_type",
            "name",
            "network",
            "network_code",
            "percentage",
            "provider",
            "poll_time",
            "port_status",
            "power_factor",
            "price_calc",
            "price_time",
            "produced_hourly",
            "pv_1_voltage",
            "pv_2_voltage",
            "rank",
            "remain_today",
            "role_status",
            "runtime",
            "runtime_seconds",
            "schedule",
            "serialnumber",
            "start_time",
            "state_of_charge",
            "state_of_health",
            "station_id",
            "station_type",
            "status",
            "solar_brand",
            "solar_model",
            "solar_sn",
            "sw_version",
            "trees",
            "tz_offset_sec",
            "voltage",
            "voltage_l1l2",
            "voltage_l1l3",
            "voltage_l2l3",
        }
    )

    def __init__(
        self,
        coordinator: AnkerSolixDataUpdateCoordinator,
        description: AnkerSolixSensorDescription,
        context: str,
        entity_type: str,
    ) -> None:
        """Initialize an Anker Solix Device coordinator entity.

        The CoordinatorEntity class provides:
        should_poll
        async_update
        async_added_to_hass
        available
        """
        super().__init__(coordinator, context)

        self.entity_description = description
        self.entity_type = entity_type
        self._attribute_name = description.key
        self._attr_attribution = f"{ATTRIBUTION}{' + MQTT' if description.mqtt else ''}"
        self._attr_unique_id = (f"{context}_{description.key}").lower()
        wwwroot = str(Path(self.coordinator.hass.config.config_dir) / "www")
        if (
            description.picture_path
            and Path(
                description.picture_path.replace(
                    AnkerSolixPicturePath.LOCALPATH, wwwroot
                )
            ).is_file()
        ):
            self._attr_entity_picture = description.picture_path
        self._attr_extra_state_attributes = None
        # Split context for nested device serials
        contexts = context.split("_")
        self._context_base = contexts[0]
        if len(contexts) > 1:
            self._context_nested = contexts[1]

        if self.entity_type == AnkerSolixEntityType.DEVICE:
            # get the device data from device context entry of coordinator data
            data = coordinator.data.get(self._context_base) or {}
            if data.get("is_subdevice"):
                self._attr_device_info = get_AnkerSolixSubdeviceInfo(
                    data, self._context_base, data.get("main_sn")
                )
            else:
                self._attr_device_info = get_AnkerSolixDeviceInfo(
                    data, self._context_base, coordinator.client.api.apisession.email
                )
            # add service attribute for manageable devices
            self._attr_supported_features: AnkerSolixEntityFeature = (
                description.feature if data.get("is_admin", False) else None
            )
            if self._attribute_name == "fittings":
                # set the correct fitting type picture for the entity
                if (
                    pn := (
                        (data.get("fittings") or {}).get(context.split("_")[1]) or {}
                    ).get("product_code")
                ) and hasattr(AnkerSolixPicturePath, pn):
                    self._attr_entity_picture = getattr(AnkerSolixPicturePath, pn)
            elif self._attribute_name == "charging_status_desc":
                # change the charing status options for matching device type
                if data.get("type") == SolixDeviceType.SOLARBANK_PPS.value:
                    self._attr_options = [status.name for status in SolarbankPpsStatus]
            # disable picture again if path does not exist to allow display of icons alternatively
            if (
                self._attr_entity_picture
                and not Path(
                    self._attr_entity_picture.replace(
                        AnkerSolixPicturePath.LOCALPATH, wwwroot
                    )
                ).is_file()
            ):
                self._attr_entity_picture = None
        elif self.entity_type == AnkerSolixEntityType.ACCOUNT:
            # get the account data from account context entry of coordinator data
            # use full context since email may contain underscores
            data = coordinator.data.get(context) or {}
            self._attr_device_info = get_AnkerSolixAccountInfo(data, context)
            # add service attribute for account entities
            self._attr_supported_features: AnkerSolixEntityFeature = description.feature
        elif self.entity_type == AnkerSolixEntityType.VEHICLE:
            # get the vehicle info data from vehicle entry of coordinator data
            data = coordinator.data.get(self._context_base) or {}
            self._attr_device_info = get_AnkerSolixVehicleInfo(
                data, self._context_base, coordinator.client.api.apisession.email
            )
        else:
            # get the site info data from site context entry of coordinator data
            data = (coordinator.data.get(self._context_base) or {}).get(
                "site_info"
            ) or {}
            self._attr_device_info = get_AnkerSolixSystemInfo(
                data, self._context_base, coordinator.client.api.apisession.email
            )
            # add service attribute for site entities
            self._attr_supported_features: AnkerSolixEntityFeature = description.feature

        self._native_value = None
        self._assumed_state = False
        self.update_state_value()
        self._last_known_value = self._native_value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update_state_value()
        super()._handle_coordinator_update()

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self._native_value

    @property
    def assumed_state(self):
        """Return the assumed state of the sensor."""
        return self._assumed_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        if (
            self.coordinator
            and (hasattr(self.coordinator, "data"))
            and self._context_base in self.coordinator.data
        ):
            # Api device data
            data = self.coordinator.data.get(self._context_base)
            if self.entity_description.mqtt and (
                mdev := self.coordinator.client.get_mqtt_device(self._context_base)
            ):
                # Combined MQTT device data, overlay prio depends on customized setting
                data = mdev.get_combined_cache(
                    api_prio=not mdev.device.get(MQTT_OVERLAY),
                    fromFile=self.coordinator.client.testmode(),
                )
            with suppress(ValueError, TypeError):
                self._attr_extra_state_attributes = self.entity_description.attrib_fn(
                    data, self.coordinator_context
                )
        return self._attr_extra_state_attributes

    @property
    def supported_features(self) -> AnkerSolixEntityFeature:
        """Flag supported features."""
        return self._attr_supported_features

    def update_state_value(self):
        """Update the state value of the sensor based on the coordinator data."""
        if self.coordinator and not (hasattr(self.coordinator, "data")):
            self._native_value = None
        elif self._context_base in self.coordinator.data:
            # Api device data
            data = self.coordinator.data.get(self._context_base)
            ignore_invalid = False
            if self.entity_description.mqtt and (
                mdev := self.coordinator.client.get_mqtt_device(self._context_base)
            ):
                # Combined MQTT device data, overlay prio depends on customized setting
                data = mdev.get_combined_cache(
                    api_prio=not mdev.device.get(MQTT_OVERLAY),
                    fromFile=self.coordinator.client.testmode(),
                )
                ignore_invalid = mdev.device.get(MQTT_OVERLAY) and mdev.is_connected
            key = self.entity_description.json_key
            with suppress(ValueError, TypeError):
                # check if FW changed for device and update device entry in registry
                # check only for single device sensor that should be common for each Solix device type
                if (
                    self._attribute_name == "sw_version"
                    and self.device_entry
                    and (
                        firmware := self.entity_description.value_fn(
                            data, key, self.coordinator_context
                        )
                    )
                ):
                    if firmware != self.state:
                        # get device registry and update the device entry attribute
                        dev_registry = dr.async_get(self.coordinator.hass)
                        dev_registry.async_update_device(
                            self.device_entry.id,
                            sw_version=firmware,
                        )
                    self._native_value = firmware
                else:
                    # update sensor unit if described by function
                    if unit := self.entity_description.unit_fn(
                        data, self.coordinator_context
                    ):
                        self._attr_native_unit_of_measurement = unit
                    if (
                        not ignore_invalid
                        and self.entity_description.check_invalid
                        and not data.get("data_valid", True)
                    ):
                        # skip update or mark unvailable
                        if not self.coordinator.config_entry.options.get(
                            CONF_SKIP_INVALID
                        ):
                            self._native_value = None
                    elif self.state_class == SensorStateClass.TOTAL_INCREASING:
                        # Fix #319: Skip energy rounding errors by cloud if decrease within suggested display precision
                        old = self._native_value
                        self._native_value = self.entity_description.value_fn(
                            data, key, self.coordinator_context
                        )
                        if old is not None and (
                            0
                            > (float(self._native_value) - float(old))
                            >= -1 / 10**self.suggested_display_precision
                        ):
                            self._native_value = old
                    else:
                        self._native_value = self.entity_description.value_fn(
                            data, key, self.coordinator_context
                        )
                        if (
                            self._native_value
                            and self.device_class == SensorDeviceClass.TEMPERATURE
                        ):
                            # Set unit of measurement as user option to allow automatic state conversion by HA core
                            if data.get("temp_unit_fahrenheit"):
                                self._sensor_option_unit_of_measurement = (
                                    UnitOfTemperature.FAHRENHEIT
                                )
                            else:
                                self._sensor_option_unit_of_measurement = (
                                    UnitOfTemperature.CELSIUS
                                )
                    # Ensure to set power sensors to None if empty strings returned
                    if (
                        self.device_class == SensorDeviceClass.POWER
                        and not self._native_value
                    ):
                        self._native_value = None

                # perform potential value conversions in testmode
                if (
                    self.coordinator.client.testmode()
                    and TEST_NUMBERVARIANCE
                    and self._native_value is not None
                    and float(self._native_value)
                ):
                    # When running in Test mode, simulate some variance for sensors with set device class
                    if self.device_class:
                        if self.device_class == SensorDeviceClass.ENUM:
                            self._native_value = choice(self.entity_description.options)
                        elif self.device_class == SensorDeviceClass.ENERGY and hasattr(
                            self, "_last_known_value"
                        ):
                            # only moderate increase from last knonw value to higher value for Energy to avoid meter reset alerts
                            self._native_value = round(
                                max(
                                    float(self._last_known_value),
                                    float(self._native_value),
                                )
                                * randrange(100, 102, 1)
                                / 100,
                                3,
                            )
                        else:
                            # value fluctuation in both directions for other classes
                            self._native_value = round(
                                float(self._native_value) * randrange(70, 130, 5) / 100,
                                3,
                            )
        else:
            self._native_value = None

        # Mark sensor availability based on a sensore value
        self._attr_available = self._native_value is not None

    async def get_system_info(self, **kwargs: Any) -> dict | None:
        """Get the actual system info from the api."""
        return await self._solix_system_service(
            service_name=SERVICE_GET_SYSTEM_INFO, **kwargs
        )

    async def get_solarbank_schedule(self, **kwargs: Any) -> dict | None:
        """Get the active solarbank schedule from the api."""
        return await self._solarbank_schedule_service(
            service_name=SERVICE_GET_SOLARBANK_SCHEDULE, **kwargs
        )

    async def clear_solarbank_schedule(self, **kwargs: Any) -> None:
        """Clear the active solarbank schedule."""
        await self._solarbank_schedule_service(
            service_name=SERVICE_CLEAR_SOLARBANK_SCHEDULE, **kwargs
        )

    async def set_solarbank_schedule(self, **kwargs: Any) -> None:
        """Set the defined solarbank schedule slot."""
        await self._solarbank_schedule_service(
            service_name=SERVICE_SET_SOLARBANK_SCHEDULE, **kwargs
        )

    async def update_solarbank_schedule(self, **kwargs: Any) -> None:
        """Update the defined solarbank schedule."""
        await self._solarbank_schedule_service(
            service_name=SERVICE_UPDATE_SOLARBANK_SCHEDULE, **kwargs
        )

    async def _solix_system_service(
        self, service_name: str, **kwargs: Any
    ) -> dict | None:
        """Execute the defined system action."""
        # Raise alerts to frontend
        if not (self.supported_features & AnkerSolixEntityFeature.SYSTEM_INFO):
            raise ServiceValidationError(
                f"The entity {self.entity_id} does not support the action {service_name}",
                translation_domain=DOMAIN,
                translation_key="service_not_supported",
                translation_placeholders={
                    "entity": self.entity_id,
                    "service": service_name,
                },
            )
        # When running in Test mode do not run services that are not supporting a testmode
        if (
            self.coordinator.client.testmode()
            and service_name != SERVICE_GET_SYSTEM_INFO
        ):
            raise ServiceValidationError(
                f"{self.entity_id} cannot be used while configuration is running in testmode",
                translation_domain=DOMAIN,
                translation_key="active_testmode",
                translation_placeholders={
                    "entity_id": self.entity_id,
                },
            )
        # When Api refresh is deactivated, do not run action to avoid kicking off other client Api token
        if not self.coordinator.client.allow_refresh():
            raise ServiceValidationError(
                f"{self.entity_id} cannot be used for requested action {service_name} while Api usage is deactivated",
                translation_domain=DOMAIN,
                translation_key="apiusage_deactivated",
                translation_placeholders={
                    "entity_id": self.entity_id,
                    "action_name": service_name,
                },
            )
        if (
            self.coordinator
            and hasattr(self.coordinator, "data")
            and self._context_base in self.coordinator.data
        ):
            if service_name == SERVICE_GET_SYSTEM_INFO:
                LOGGER.debug("%s action will be applied", service_name)
                # Wait until client cache is valid
                await self.coordinator.client.validate_cache()
                if kwargs.get(INCLUDE_CACHE):
                    result = (
                        await self.coordinator.client.api.update_sites(
                            siteId=self._context_base,
                            fromFile=self.coordinator.client.testmode(),
                            exclude=set(self.coordinator.client.exclude_categories),
                        )
                    ).get(self._context_base) or None
                else:
                    result = await self.coordinator.client.api.get_scene_info(
                        siteId=self._context_base,
                        fromFile=self.coordinator.client.testmode(),
                    )
                return {"system_info": result}

            raise ServiceValidationError(
                f"The entity {self.entity_id} does not support the action {service_name}",
                translation_domain=DOMAIN,
                translation_key="service_not_supported",
                translation_placeholders={
                    "entity": self.entity_id,
                    "service": service_name,
                },
            )
        return None

    async def _solarbank_schedule_service(
        self, service_name: str, **kwargs: Any
    ) -> dict | None:
        """Execute the defined solarbank schedule action."""
        # Raise alerts to frontend
        if not (self.supported_features & AnkerSolixEntityFeature.SOLARBANK_SCHEDULE):
            raise ServiceValidationError(
                f"The entity {self.entity_id} does not support the action {service_name}",
                translation_domain=DOMAIN,
                translation_key="service_not_supported",
                translation_placeholders={
                    "entity": self.entity_id,
                    "service": service_name,
                },
            )
        # When running in Test mode do not run services that are not supporting a testmode
        if self.coordinator.client.testmode() and service_name not in [
            SERVICE_SET_SOLARBANK_SCHEDULE,
            SERVICE_UPDATE_SOLARBANK_SCHEDULE,
            SERVICE_CLEAR_SOLARBANK_SCHEDULE,
            SERVICE_GET_SOLARBANK_SCHEDULE,
        ]:
            raise ServiceValidationError(
                f"{self.entity_id} cannot be used while configuration is running in testmode",
                translation_domain=DOMAIN,
                translation_key="active_testmode",
                translation_placeholders={
                    "entity_id": self.entity_id,
                },
            )
        # When Api refresh is deactivated, do not run action to avoid kicking off other client Api token
        if not self.coordinator.client.allow_refresh():
            raise ServiceValidationError(
                f"{self.entity_id} cannot be used for requested action {service_name} while Api usage is deactivated",
                translation_domain=DOMAIN,
                translation_key="apiusage_deactivated",
                translation_placeholders={
                    "entity_id": self.entity_id,
                    "action_name": service_name,
                },
            )
        if (
            self.coordinator
            and hasattr(self.coordinator, "data")
            and self._context_base in self.coordinator.data
        ):
            data: dict = self.coordinator.data.get(self._context_base)
            generation: int = (
                2
                if data.get("type") == SolixDeviceType.COMBINER_BOX.value
                else int(data.get("generation") or 0)
            )
            siteId = data.get("site_id") or ""
            if service_name in [
                SERVICE_SET_SOLARBANK_SCHEDULE,
                SERVICE_UPDATE_SOLARBANK_SCHEDULE,
            ]:
                plan = kwargs.get(PLAN)
                # Raise error if selected (active) plan not usable for the service
                if plan not in {
                    SolarbankRatePlan.smartplugs,
                    SolarbankRatePlan.manual,
                } and data.get("preset_usage_mode") not in {None, 1, 2, 3}:
                    raise ServiceValidationError(
                        f"The action {service_name} cannot be executed: {'Selected plan [' + str(plan) + '] of [' + self.entity_id + '] not usable for this action'}.",
                        translation_domain=DOMAIN,
                        translation_key="slot_time_error",
                        translation_placeholders={
                            "service": service_name,
                            "error": "Selected plan ["
                            + str(plan)
                            + "] of ["
                            + self.entity_id
                            + "] not usable for this action",
                        },
                    )
                start_time = kwargs.get(START_TIME)
                end_time = kwargs.get(END_TIME)
                if start_time and end_time:
                    if start_time < end_time:
                        weekdays = kwargs.get(WEEK_DAYS)
                        load = kwargs.get(APPLIANCE_LOAD)
                        dev_load = kwargs.get(DEVICE_LOAD)
                        allow_export = kwargs.get(ALLOW_EXPORT)
                        discharge_prio = kwargs.get(DISCHARGE_PRIORITY)
                        prio = kwargs.get(CHARGE_PRIORITY_LIMIT)
                        # check if now is in given time range and ensure preset increase is limited by min interval
                        now = datetime.now().astimezone()
                        # consider device timezone offset when checking for actual slot
                        tz_offset = (self.coordinator.data.get(siteId) or {}).get(
                            "energy_offset_tz"
                        ) or 0
                        start_time.astimezone()
                        # get old device load, which is none for single solarbanks, use old system preset instead
                        old_dev = data.get("preset_device_output_power") or data.get(
                            "preset_system_output_power"
                        )
                        old_dev = dev_load if old_dev is None else old_dev
                        # set the system load that should be checked for increase
                        check_load = (
                            load
                            if dev_load is None
                            else int(
                                self._last_schedule_service_value + (dev_load - old_dev)
                            )
                            if load is None
                            and self._last_schedule_service_value is not None
                            else None
                        )
                        if (
                            self._last_schedule_service_value
                            and check_load
                            and check_load > int(self._last_schedule_service_value)
                            and start_time.astimezone().time()
                            <= (now + timedelta(seconds=tz_offset)).time()
                            < end_time.astimezone().time()
                            and now
                            < (
                                self.hass.states.get(self.entity_id).last_changed
                                + timedelta(seconds=_SCAN_INTERVAL_MIN)
                            )
                        ):
                            LOGGER.debug(
                                "%s cannot be increased to %s because minimum change delay of %s seconds is not passed",
                                self.entity_id,
                                check_load,
                                _SCAN_INTERVAL_MIN,
                            )
                            # Raise alert to frontend
                            raise ServiceValidationError(
                                f"{self.entity_id} cannot be increased to {check_load} because minimum change delay of {_SCAN_INTERVAL_MIN} seconds is not passed",
                                translation_domain=DOMAIN,
                                translation_key="increase_blocked",
                                translation_placeholders={
                                    "entity_id": self.entity_id,
                                    "value": check_load,
                                    "delay": _SCAN_INTERVAL_MIN,
                                },
                            )

                        LOGGER.debug("%s action will be applied", service_name)
                        # Wait until client cache is valid
                        await self.coordinator.client.validate_cache()
                        if generation >= 2:
                            # SB2 schedule action
                            # Map action keys to api slot keys
                            slot = Solarbank2Timeslot(
                                start_time=start_time,
                                end_time=end_time,
                                appliance_load=load,
                                weekdays=set(weekdays) if weekdays else None,
                            )
                            if service_name == SERVICE_SET_SOLARBANK_SCHEDULE:
                                result = (
                                    await self.coordinator.client.api.set_sb2_home_load(
                                        siteId=siteId,
                                        deviceSn=self._context_base,
                                        set_slot=slot,
                                        plan_name=plan,
                                        toFile=self.coordinator.client.testmode(),
                                    )
                                )
                            elif service_name == SERVICE_UPDATE_SOLARBANK_SCHEDULE:
                                result = (
                                    await self.coordinator.client.api.set_sb2_home_load(
                                        siteId=siteId,
                                        deviceSn=self._context_base,
                                        insert_slot=slot,
                                        plan_name=plan,
                                        toFile=self.coordinator.client.testmode(),
                                    )
                                )
                            else:
                                result = False
                        else:
                            # SB1 schedule action
                            # Raise error if action currently not usable for active schedule
                            if (
                                data.get("cascaded")
                                and data.get("preset_allow_export") is None
                            ):
                                raise ServiceValidationError(
                                    f"The action {service_name} cannot be executed: {'Active schedule of [' + self.entity_id + '] not usable for this action'}.",
                                    translation_domain=DOMAIN,
                                    translation_key="slot_time_error",
                                    translation_placeholders={
                                        "service": service_name,
                                        "error": "Active schedule of ["
                                        + self.entity_id
                                        + "] not usable for this action",
                                    },
                                )
                            # Map action keys to api slot keys
                            slot = SolarbankTimeslot(
                                start_time=start_time,
                                end_time=end_time,
                                appliance_load=load,
                                device_load=dev_load,
                                allow_export=allow_export,
                                discharge_priority=discharge_prio,
                                charge_priority_limit=prio,
                            )
                            if service_name == SERVICE_SET_SOLARBANK_SCHEDULE:
                                result = (
                                    await self.coordinator.client.api.set_home_load(
                                        siteId=siteId,
                                        deviceSn=self._context_base,
                                        set_slot=slot,
                                        toFile=self.coordinator.client.testmode(),
                                    )
                                )
                            elif service_name == SERVICE_UPDATE_SOLARBANK_SCHEDULE:
                                result = (
                                    await self.coordinator.client.api.set_home_load(
                                        siteId=siteId,
                                        deviceSn=self._context_base,
                                        insert_slot=slot,
                                        toFile=self.coordinator.client.testmode(),
                                    )
                                )
                            else:
                                result = False

                        # log resulting schedule if testmode returned dict
                        if isinstance(result, dict) and ALLOW_TESTMODE:
                            LOGGER.info(
                                "%s: Applied schedule for action %s:\n%s",
                                "TESTMODE"
                                if self.coordinator.client.testmode()
                                else "LIVEMODE",
                                service_name,
                                json.dumps(
                                    result,
                                    indent=2 if len(json.dumps(result)) < 200 else None,
                                ),
                            )
                        # update sites was required to get applied output power fields, they are not provided with get_device_parm endpoint
                        # which fetches new schedule after update. Now the output power fields are updated along with a schedule update in the cache
                        # await self.coordinator.client.api.update_sites(
                        #     siteId=siteId,
                        #     fromFile=self.coordinator.client.testmode(),
                        # )
                        # trigger coordinator update with api dictionary data
                        await self.coordinator.async_refresh_data_from_apidict()
                        # refresh last applied system load
                        if result:
                            self._last_schedule_service_value = (
                                self.coordinator.data.get(self._context_base) or {}
                            ).get("preset_system_output_power") or None
                    else:
                        raise ServiceValidationError(
                            f"The action {service_name} cannot be executed: {'start_time must be earlier than end_time'}.",
                            translation_domain=DOMAIN,
                            translation_key="slot_time_error",
                            translation_placeholders={
                                "service": service_name,
                                "error": "start_time must be earlier than end_time",
                            },
                        )
                else:
                    raise ServiceValidationError(
                        f"The action {service_name} cannot be executed: {'start_time or end_time missing'}.",
                        translation_domain=DOMAIN,
                        translation_key="slot_time_error",
                        translation_placeholders={
                            "service": service_name,
                            "error": "start_time or end_time missing",
                        },
                    )

            elif service_name == SERVICE_GET_SOLARBANK_SCHEDULE:
                LOGGER.debug("%s action will be applied", service_name)
                # Wait until client cache is valid
                await self.coordinator.client.validate_cache()
                if generation >= 2 and (data.get("schedule") or {}):
                    # get SB2 schedule
                    result = (
                        await self.coordinator.client.api.get_device_parm(
                            siteId=siteId,
                            paramType=SolixParmType.SOLARBANK_2_SCHEDULE.value,
                            deviceSn=self._context_base,
                            fromFile=self.coordinator.client.testmode(),
                        )
                        or {}
                    ).get("param_data")
                else:
                    result = (
                        await self.coordinator.client.api.get_device_load(
                            siteId=siteId,
                            deviceSn=self._context_base,
                            fromFile=self.coordinator.client.testmode(),
                        )
                        or {}
                    ).get("home_load_data")
                # trigger coordinator update with api dictionary data
                await self.coordinator.async_refresh_data_from_apidict()
                return {"schedule": result}

            elif service_name == SERVICE_CLEAR_SOLARBANK_SCHEDULE:
                LOGGER.debug("%s action will be applied", service_name)
                # Wait until client cache is valid
                await self.coordinator.client.validate_cache()
                if generation >= 2:
                    # Clear SB2 schedule
                    if data.get("schedule") or {}:
                        plan = kwargs.get(PLAN)
                        weekdays = kwargs.get(WEEK_DAYS)
                        result = await self.coordinator.client.api.set_sb2_home_load(
                            siteId=siteId,
                            deviceSn=self._context_base,
                            plan_name=plan,
                            set_slot=Solarbank2Timeslot(
                                start_time=None,
                                end_time=None,
                                weekdays=set(weekdays) if weekdays else None,
                            ),
                            toFile=self.coordinator.client.testmode(),
                        )
                        # log resulting schedule if in testmode
                        if isinstance(result, dict) and ALLOW_TESTMODE:
                            LOGGER.info(
                                "%s: Applied schedule for action %s:\n%s",
                                "TESTMODE"
                                if self.coordinator.client.testmode()
                                else "LIVEMODE",
                                service_name,
                                json.dumps(
                                    result,
                                    indent=2 if len(json.dumps(result)) < 200 else None,
                                ),
                            )
                else:
                    # clear SB 1 schedule
                    # Wait until client cache is valid
                    await self.coordinator.client.validate_cache()
                    # No need to Raise error if cascaded, since clearing will directly be done against correct Api device param for custom SB1 schedule
                    result = await self.coordinator.client.api.set_device_parm(
                        siteId=siteId,
                        paramData={"ranges": []},
                        deviceSn=self._context_base,
                        toFile=self.coordinator.client.testmode(),
                    )
                    # log resulting schedule if testmode returned dict
                    if isinstance(result, dict) and ALLOW_TESTMODE:
                        LOGGER.info(
                            "%s: Applied schedule for action %s:\n%s",
                            "TESTMODE"
                            if self.coordinator.client.testmode()
                            else "LIVEMODE",
                            service_name,
                            json.dumps(
                                result,
                                indent=2 if len(json.dumps(result)) < 200 else None,
                            ),
                        )

                # update sites was required to get applied output power fields, they are not provided with get_device_parm endpoint
                # which fetches new schedule after update. Now the output power fields are updated along with a schedule update in the cache
                # await self.coordinator.client.api.update_sites(
                #     siteId=siteId,
                #     fromFile=self.coordinator.client.testmode(),
                # )
                # trigger coordinator update with api dictionary data
                await self.coordinator.async_refresh_data_from_apidict()

            else:
                raise ServiceValidationError(
                    f"The entity {self.entity_id} does not support the action {service_name}",
                    translation_domain=DOMAIN,
                    translation_key="service_not_supported",
                    translation_placeholders={
                        "entity": self.entity_id,
                        "service": service_name,
                    },
                )
        return None


class AnkerSolixRestoreSensor(AnkerSolixSensor, RestoreSensor):
    """Represents an restore sensor entity for Anker Solix site and device data."""

    coordinator: AnkerSolixDataUpdateCoordinator
    entity_description: AnkerSolixSensorDescription

    def __init__(
        self,
        coordinator: AnkerSolixDataUpdateCoordinator,
        description: AnkerSolixSensorDescription,
        context: str,
        entity_type: str,
    ) -> None:
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(coordinator, description, context, entity_type)
        self._assumed_state = True

    async def async_added_to_hass(self) -> None:
        """Load the last known state when added to hass."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) and (
            last_data := await self.async_get_last_sensor_data()
        ):
            # handle special entity restore actions for customized attributes even if old state was unknown
            if self._attribute_name == "solar_forecast_today":
                attribute = "forecast_hourly"
                if (
                    attr_value := last_state.attributes.get(attribute)
                ) and self.extra_state_attributes.get(attribute) != attr_value:
                    LOGGER.info(
                        "Restored state attribute '%s' of entity '%s' to: %s",
                        attribute,
                        self.entity_id,
                        attr_value,
                    )
                    self.coordinator.client.api.customizeCacheId(
                        id=self.coordinator_context,
                        key="pv_forecast_details",
                        value={"trend": attr_value},
                    )
                    await self.coordinator.async_refresh_data_from_apidict(delayed=True)
            elif (
                last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
                and self._native_value is not None
            ):
                # set the customized value if it was modified
                if self._native_value != last_data.native_value:
                    self._native_value = last_data.native_value
                    LOGGER.info(
                        "Restored state value of entity '%s' to: %s",
                        self.entity_id,
                        self._native_value,
                    )
                    self.coordinator.client.api.customizeCacheId(
                        id=self.coordinator_context,
                        key=self.entity_description.json_key,
                        value=str(last_data.native_value),
                    )
                    await self.coordinator.async_refresh_data_from_apidict(delayed=True)
