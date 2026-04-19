"""Switch platform for the Anker Charger integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_EXCLUDE,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CREATE_ALL_ENTITIES,
    DOMAIN,
    LOGGER,
    MQTT_OVERLAY,
)
from .coordinator import AnkerSolixDataUpdateCoordinator
from .entity import (
    AnkerSolixEntityFeature,
    AnkerSolixEntityRequiredKeyMixin,
    AnkerSolixEntityType,
    get_AnkerSolixAccountInfo,
    get_AnkerSolixDeviceInfo,
)
from .solixapi.apitypes import SolixDeviceType
from .solixapi.mqtt_device import SolixMqttDevice
from .solixapi.mqttcmdmap import SolixMqttCommands


@dataclass(frozen=True)
class AnkerSolixSwitchDescription(
    SwitchEntityDescription, AnkerSolixEntityRequiredKeyMixin
):
    """Switch entity description with optional keys."""

    feature: AnkerSolixEntityFeature | None = None
    restore: bool = False
    mqtt: bool = False
    mqtt_cmd: str | None = None
    mqtt_cmd_parm: str | None = None
    api_cmd: bool | None = None
    inverted: bool = False

    # Use optionally to provide function for value calculation or lookup of nested values
    value_fn: Callable[[dict, str], bool | None] = lambda d, jk: d.get(jk)
    attrib_fn: Callable[[dict, str], dict | None] = lambda d, ctx: None
    exclude_fn: Callable[[set, dict], bool] = lambda s, d: False
    force_creation_fn: Callable[[dict, str], bool] = lambda d, jk: False


DEVICE_SWITCHES = [
    AnkerSolixSwitchDescription(
        key="display_switch",
        translation_key="display_switch",
        json_key="display_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.SWITCH,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.display_switch,
    ),
    AnkerSolixSwitchDescription(
        key="port_memory_switch",
        translation_key="port_memory_switch",
        json_key="port_memory_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.SWITCH,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.port_memory_switch,
    ),
    AnkerSolixSwitchDescription(
        key="usbc_1_switch",
        translation_key="usbc_1_switch",
        json_key="usbc_1_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.SWITCH,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.usbc_1_port_switch,
    ),
    AnkerSolixSwitchDescription(
        key="usbc_2_switch",
        translation_key="usbc_2_switch",
        json_key="usbc_2_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.SWITCH,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.usbc_2_port_switch,
    ),
    AnkerSolixSwitchDescription(
        key="usbc_3_switch",
        translation_key="usbc_3_switch",
        json_key="usbc_3_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.SWITCH,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.usbc_3_port_switch,
    ),
    AnkerSolixSwitchDescription(
        key="usbc_4_switch",
        translation_key="usbc_4_switch",
        json_key="usbc_4_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.SWITCH,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.usbc_4_port_switch,
    ),
    AnkerSolixSwitchDescription(
        key="usba_switch",
        translation_key="usba_switch",
        json_key="usba_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.SWITCH,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.usba_port_switch,
    ),
    AnkerSolixSwitchDescription(
        key="ac_1_switch",
        translation_key="ac_1_switch",
        json_key="ac_1_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.OUTLET,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.ac_1_port_switch,
    ),
    AnkerSolixSwitchDescription(
        key="ac_2_switch",
        translation_key="ac_2_switch",
        json_key="ac_2_switch",
        exclude_fn=lambda s, d: not ({d.get("type")} - s),
        device_class=SwitchDeviceClass.OUTLET,
        mqtt=True,
        mqtt_cmd=SolixMqttCommands.ac_2_port_switch,
    ),
]


ACCOUNT_SWITCHES = [
    AnkerSolixSwitchDescription(
        key="allow_refresh",
        translation_key="allow_refresh",
        json_key="allow_refresh",
        entity_category=EntityCategory.DIAGNOSTIC,
        feature=AnkerSolixEntityFeature.ACCOUNT_INFO,
        force_creation_fn=lambda d, _: True,
        value_fn=lambda d, _: len(d) > 0,
        attrib_fn=lambda d, _: {
            "requests_last_min": d.get("requests_last_min"),
            "requests_last_hour": d.get("requests_last_hour"),
        },
    ),
]

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
            if data.get("type") == SolixDeviceType.ACCOUNT.value:
                # Unique key for account entry in data
                entity_type = AnkerSolixEntityType.ACCOUNT
                entity_list = ACCOUNT_SWITCHES
            else:
                # device_sn entry in data
                entity_type = AnkerSolixEntityType.DEVICE
                entity_list = DEVICE_SWITCHES
                # get MQTT device combined values for creation of entities
                if mdev := coordinator.client.get_mqtt_device(sn=context):
                    mdata = mdev.get_combined_cache()

            for description in (
                desc
                for desc in entity_list
                if bool(CREATE_ALL_ENTITIES)
                or (
                    not desc.exclude_fn(set(entry.options.get(CONF_EXCLUDE, [])), data)
                    and (
                        desc.force_creation_fn(data, desc.json_key)
                        # filter MQTT entities and provide combined or only api cache
                        # Entities that should not be created without MQTT data need to use exclude option
                        or (
                            desc.mqtt
                            and desc.value_fn(mdata or data, desc.json_key) is not None
                            # include MQTT command switch entities only if switch options or also using Api command
                            and (
                                desc.api_cmd
                                or not (
                                    mdev
                                    and desc.mqtt_cmd
                                    and not mdev.cmd_is_switch(
                                        desc.mqtt_cmd, parm=desc.mqtt_cmd_parm
                                    )
                                )
                            )
                        )
                        # filter API only entities
                        or (
                            not desc.mqtt
                            and desc.value_fn(data, desc.json_key) is not None
                        )
                    )
                )
            ):
                if description.restore:
                    entity = AnkerSolixRestoreSwitch(
                        coordinator, description, context, entity_type
                    )
                else:
                    entity = AnkerSolixSwitch(
                        coordinator, description, context, entity_type
                    )
                entities.append(entity)

    # create the sensors from the list
    async_add_entities(entities)


class AnkerSolixSwitch(CoordinatorEntity, SwitchEntity):
    """Anker Charger switch class."""

    coordinator: AnkerSolixDataUpdateCoordinator
    entity_description: AnkerSolixSwitchDescription
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset(
        {
            "requests_last_min",
            "requests_last_hour",
            "customized",
        }
    )

    def __init__(
        self,
        coordinator: AnkerSolixDataUpdateCoordinator,
        description: AnkerSolixSwitchDescription,
        context: str,
        entity_type: str,
    ) -> None:
        """Initialize the switch class."""
        super().__init__(coordinator, context)

        self._attribute_name = description.key
        self._attr_attribution = f"{ATTRIBUTION}{' + MQTT' if description.mqtt else ''}"
        self._attr_unique_id = (f"{context}_{description.key}").lower()
        self.entity_description = description
        self.entity_type = entity_type
        self.last_run: datetime | None = None
        self._attr_extra_state_attributes = None

        if self.entity_type == AnkerSolixEntityType.DEVICE:
            # get the device data from device context entry of coordinator data
            data = coordinator.data.get(context) or {}
            self._attr_device_info = get_AnkerSolixDeviceInfo(
                data, context, coordinator.client.api.apisession.email
            )
            # add service attribute for manageable devices
            self._attr_supported_features: AnkerSolixEntityFeature = (
                description.feature if data.get("is_admin", False) else None
            )
        elif self.entity_type == AnkerSolixEntityType.ACCOUNT:
            # get the account data from account context entry of coordinator data
            data = coordinator.data.get(context) or {}
            self._attr_device_info = get_AnkerSolixAccountInfo(data, context)
            # add service attribute for account entities
            self._attr_supported_features: AnkerSolixEntityFeature = description.feature

        self._attr_is_on = None
        self.update_state_value()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.update_state_value()
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the entity."""
        if (
            self.coordinator
            and (hasattr(self.coordinator, "data"))
            and self.coordinator_context in self.coordinator.data
        ):
            # Api device data
            data = self.coordinator.data.get(self.coordinator_context)
            if self.entity_description.mqtt and (
                mdev := self.coordinator.client.get_mqtt_device(
                    self.coordinator_context
                )
            ):
                # Combined MQTT device data, overlay prio depends on customized setting
                data = mdev.get_combined_cache(
                    api_prio=not mdev.device.get(MQTT_OVERLAY),
                )
            with suppress(ValueError, TypeError):
                self._attr_extra_state_attributes = self.entity_description.attrib_fn(
                    data, self.coordinator_context
                )
        return self._attr_extra_state_attributes




    def update_state_value(self):
        """Update the state value of the switch based on the coordinator data."""
        if self.coordinator and not (hasattr(self.coordinator, "data")):
            self._attr_is_on = None
        elif self.coordinator_context in self.coordinator.data:
            # Api device data
            data = self.coordinator.data.get(self.coordinator_context)
            if self.entity_description.mqtt and (
                mdev := self.coordinator.client.get_mqtt_device(
                    self.coordinator_context
                )
            ):
                # Combined MQTT device data, overlay prio depends on customized setting
                data = mdev.get_combined_cache(
                    api_prio=not mdev.device.get(MQTT_OVERLAY),
                )
            key = self.entity_description.json_key
            self._attr_is_on = self.entity_description.value_fn(data, key)
        else:
            self._attr_is_on = self.entity_description.value_fn(
                self.coordinator.data, self.entity_description.json_key
            )
        if self._attr_is_on is not None:
            # invert the state for inverted switch entity
            self._attr_is_on ^= self.entity_description.inverted

        # Mark availability based on value
        self._attr_available = self._attr_is_on is not None

    async def async_turn_on(self, **_: any) -> None:
        """Turn on the switch."""
        await self._async_toggle(enable=True)

    async def async_turn_off(self, **_: any) -> None:
        """Turn off the switch."""
        await self._async_toggle(enable=False)

    async def _async_toggle(self, enable: bool) -> None:
        """Enable or disable the entity."""
        # Skip Api calls if entity does not change
        if self._attr_is_on in [None, enable]:
            return
        if self._attribute_name == "allow_refresh":
            await self.coordinator.async_execute_command(
                command=self.entity_description.key,
                option=enable ^ self.entity_description.inverted,
            )
            return
        # Wait until client cache is valid before applying any api change
        await self.coordinator.client.validate_cache()
        mdev = self.coordinator.client.get_mqtt_device(self.coordinator_context)
        if self.entity_description.restore:
            LOGGER.info(
                "%s will be %s",
                self.entity_id,
                "enabled" if enable else "disabled",
            )
            # Customize cache if restore entity
            value = enable ^ self.entity_description.inverted
            self.coordinator.client.api.customizeCacheId(
                id=self.coordinator_context,
                key=self.entity_description.json_key,
                value=value,
            )
            await self.coordinator.async_refresh_data_from_apidict()
        elif self._attribute_name == "auto_upgrade":
            await self.coordinator.client.api.set_auto_upgrade(
                devices={
                    self.coordinator_context: enable ^ self.entity_description.inverted
                }
            )
            await self.coordinator.async_refresh_data_from_apidict()
        # Trigger MQTT commands depending on changed entity
        elif self.entity_description.mqtt_cmd and mdev:
            LOGGER.debug(
                "'%s' will be %s via MQTT command '%s'",
                self.entity_id,
                "enabled" if enable else "disabled",
                self.entity_description.mqtt_cmd,
            )
            await self._async_mqtt_toggle(mdev=mdev, enable=enable)

    async def _async_mqtt_toggle(
        self,
        mdev: SolixMqttDevice,
        enable: bool,
        cmd: str | None = None,
        parm: str | None = None,
        parm_map: dict | None = None,
    ) -> dict | None:
        """Enable or disable the entity via MQTT device control."""
        resp = None
        if not isinstance(cmd, str):
            cmd = self.entity_description.mqtt_cmd
        if not isinstance(parm, str):
            parm = self.entity_description.mqtt_cmd_parm
        try:
            cmdvalue = enable ^ self.entity_description.inverted
            resp = await mdev.run_command(
                cmd=cmd,
                parm=parm,
                value=1 if cmdvalue else 0,
                parm_map=parm_map,
            )
            if isinstance(resp, dict):
                # copy the changed state(s) of the mock response into device cache to avoid flip back of entity until real state is received
                for key, val in resp.items():
                    if key in mdev.mqttdata:
                        mdev.mqttdata[key] = val
                # delay status request to allow device to process the command first,
                # avoiding a stale 0a00 response that overwrites the mock state
                await asyncio.sleep(2)
                # trigger status request to get updated MQTT message
                await mdev.status_request()
            else:
                LOGGER.error(
                    "'%s' could not be toggled via MQTT command '%s'",
                    self.entity_id,
                    cmd,
                )
        except (ValueError, TypeError) as err:
            LOGGER.error(
                "'%s' could not be toggled via MQTT command '%s':\n%s",
                self.entity_id,
                cmd,
                str(err),
            )
        return resp




class AnkerSolixRestoreSwitch(AnkerSolixSwitch, RestoreEntity):
    """Anker Charger switch class with restore capability."""

    def __init__(
        self,
        coordinator: AnkerSolixDataUpdateCoordinator,
        description: AnkerSolixSwitchDescription,
        context: str,
        entity_type: str,
    ) -> None:
        """Initialize the switch class."""
        super().__init__(coordinator, description, context, entity_type)
        self._assumed_state = True

    async def async_added_to_hass(self) -> None:
        """Load the last known state when added to hass."""
        await super().async_added_to_hass()
        if last_state := await self.async_get_last_state():
            # First try to get customization from state attributes if last state was unknown
            if last_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                if (customized := last_state.attributes.get("customized")) is not None:
                    last_state.state = STATE_ON if customized else STATE_OFF
            if (
                last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
                and self._attr_is_on is not None
            ):
                # set the customized value if it was modified
                # NOTE: State may have string representation of boolean according to device class
                if self._attr_is_on != (last_state.state == STATE_ON):
                    self._attr_is_on = last_state.state == STATE_ON
                    LOGGER.info(
                        "Restored state value of entity '%s' to: %s",
                        self.entity_id,
                        last_state.state,
                    )
                    self.coordinator.client.api.customizeCacheId(
                        id=self.coordinator_context,
                        key=self.entity_description.json_key,
                        value=self._attr_is_on,
                    )
                    await self.coordinator.async_refresh_data_from_apidict(delayed=True)
