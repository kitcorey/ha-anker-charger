"""DataUpdateCoordinator for the Anker A91B2 charger integration."""

from __future__ import annotations

from asyncio import TimerHandle, run_coroutine_threadsafe, sleep
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import (
    AnkerSolixApiClient,
    AnkerSolixApiClientAuthenticationError,
    AnkerSolixApiClientCommunicationError,
    AnkerSolixApiClientError,
    AnkerSolixApiClientRetryExceededError,
)
from .const import DOMAIN, LOGGER, PLATFORMS


class AnkerSolixDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinate bind_devices polling + MQTT-driven entity updates."""

    config_entry: ConfigEntry
    client: AnkerSolixApiClient
    update_handler: TimerHandle | None
    registered_devices: set
    mqtt_values: int

    def __init__(
        self,
        hass: HomeAssistant,
        client: AnkerSolixApiClient,
        config_entry: ConfigEntry,
        update_interval: int,
    ) -> None:
        self.config_entry = config_entry
        self.client = client
        self.update_handler = None
        self.registered_devices = set()
        self.mqtt_values = 0
        self._first_refresh_done = False

        super().__init__(
            hass=hass,
            logger=LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.title}",
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_update_data(self) -> dict:
        """Fetch the latest device list and keep MQTT alive."""
        try:
            # Register the MQTT update callback once the client exists.
            if not self.client.api.mqtt_update_callback():
                self.client.api.mqtt_update_callback(self.update_callback)
            data = await self.client.async_get_data()
            ids = set(data.keys())
            mcount = self.client.get_mqtt_valuecount()

            if not self._first_refresh_done:
                self.registered_devices = ids
                self.mqtt_values = mcount
                self._first_refresh_done = True
            elif ids - self.registered_devices or mcount > self.mqtt_values:
                LOGGER.debug(
                    "Coordinator %s found additional %s, reloading platforms to setup entities",
                    self.client.api.apisession.nickname,
                    "MQTT values" if mcount > self.mqtt_values else "devices",
                )
                await self.async_reload_config(register_devices=data)
            elif (
                ids
                and self.registered_devices
                and (removed := self.registered_devices - ids)
            ):
                await self.async_remove_device(devices=removed)
            elif mcount < self.mqtt_values:
                LOGGER.debug(
                    "Coordinator %s found %s of %s registered MQTT values, adjusting counter",
                    self.client.api.apisession.nickname,
                    mcount,
                    self.mqtt_values,
                )
                self.mqtt_values = mcount
        except (
            AnkerSolixApiClientAuthenticationError,
            AnkerSolixApiClientRetryExceededError,
        ) as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except AnkerSolixApiClientCommunicationError as exception:
            raise UpdateFailed(exception) from exception
        except AnkerSolixApiClientError as exception:
            raise UpdateFailed(exception) from exception
        else:
            return data

    async def async_refresh_data_from_apidict(self, delayed: bool = False) -> None:
        """Refresh self.data from the client's cache, optionally debouncing listeners."""
        self.data = await self.client.async_get_data(from_cache=True)
        if delayed and not self.update_handler:
            self.update_handler = self.hass.loop.call_later(
                delay=2.0, callback=self.async_update_listeners
            )
            LOGGER.debug(
                "Coordinator %s delayed listener update for 2 seconds",
                self.client.api.apisession.nickname,
            )
            return
        if self.update_handler:
            if self.hass.loop.time() - self.update_handler.when() <= 0:
                LOGGER.debug(
                    "Coordinator %s skipped listener update due to active delayed processing",
                    self.client.api.apisession.nickname,
                )
                return
            self.update_handler = None
        self.async_update_listeners()

    async def async_refresh_device_details(
        self, reset_cache: bool = False, categories: set | str | None = None
    ) -> None:
        """Force a device-details refresh (optionally resetting caches)."""
        data = await self.client.async_get_data(
            device_details=True, reset_cache=reset_cache
        )
        if reset_cache:
            self.data = data
            self.mqtt_values = self.client.get_mqtt_valuecount()
            await self.async_reload_config(register_devices=data)
            return

        self.async_set_updated_data(data)
        ids = set(data.keys())
        mcount = self.client.get_mqtt_valuecount()
        if ids - self.registered_devices or mcount > self.mqtt_values:
            LOGGER.debug(
                "Coordinator %s found additional %s, reloading platforms to setup entities",
                self.client.api.apisession.nickname,
                "MQTT values" if mcount > self.mqtt_values else "devices",
            )
            await self.async_reload_config(register_devices=data)
        elif (
            ids
            and self.registered_devices
            and (removed := self.registered_devices - ids)
        ):
            await self.async_remove_device(devices=removed)

    def update_callback(self, sn: str | None = None, **args) -> None:
        """MQTT session calls this when new device values arrive."""
        LOGGER.debug(
            "Coordinator %s received new MQTT data for device %s:\n%s",
            self.client.api.apisession.nickname,
            sn,
            self.client.get_mqtt_device(sn).mqttdata
            if sn in self.client.mqtt_devices
            else {},
        )
        run_coroutine_threadsafe(
            self.async_refresh_data_from_apidict(delayed=True), self.hass.loop
        )

    async def async_shutdown(self) -> None:
        """Close the MQTT session before HA tears down the coordinator."""
        if self.client and self.client.api:
            self.client.api.clearCaches()
        await super().async_shutdown()

    async def async_reload_config(
        self, register_devices: set | dict | None = None
    ) -> bool:
        """Reload the integration platforms so new entities/devices get created."""
        loaded_entry = bool(
            [
                e
                for e in self.hass.config_entries.async_loaded_entries(DOMAIN)
                if e.entry_id == self.config_entry.entry_id
            ]
        )
        if loaded_entry and await self.hass.config_entries.async_unload_platforms(
            self.config_entry, PLATFORMS
        ):
            await self.hass.config_entries.async_forward_entry_setups(
                self.config_entry, PLATFORMS
            )
            self.registered_devices = (
                register_devices
                if isinstance(register_devices, set)
                else set(register_devices.keys())
                if isinstance(register_devices, dict)
                else set()
            )
            self.mqtt_values = self.client.get_mqtt_valuecount()
            return True
        return False

    async def async_remove_device(self, devices: set) -> None:
        """Drop registry entries for devices no longer in the API cache."""
        device_entries = dr.async_entries_for_config_entry(
            dr.async_get(self.hass), self.config_entry.entry_id
        )
        for dev_entry in [
            dev for dev in device_entries if dev.serial_number in devices
        ]:
            if not any(
                identifier
                for identifier in dev_entry.identifiers
                if identifier[0] == DOMAIN
                for device_serial in self.data
                if device_serial == identifier[1]
            ):
                dr.async_get(self.hass).async_update_device(
                    dev_entry.id,
                    remove_config_entry_id=self.config_entry.entry_id,
                )
                self.registered_devices.discard(dev_entry.serial_number)
                self.client.mqtt_devices.pop(dev_entry.serial_number, None)
                self.mqtt_values = self.client.get_mqtt_valuecount()
                LOGGER.warning(
                    "Api Coordinator %s removed orphaned %s device %s, ID %s",
                    self.config_entry.title,
                    dev_entry.model,
                    dev_entry.name,
                    dev_entry.serial_number,
                )

    async def async_execute_command(
        self, command: str, option: Any = None
    ) -> bool | None:
        """Dispatch commands issued via switches (notably allow_refresh)."""
        match command:
            case "refresh_device":
                await self.async_refresh_device_details()
            case "allow_refresh":
                if isinstance(option, bool):
                    self.client.allow_refresh(allow=option)
                    if option:
                        await self.async_refresh_data_from_apidict()
                        await self.async_refresh_device_details(reset_cache=True)
                    else:
                        await self.async_refresh_data_from_apidict()
        return None

    async def async_refresh_delay(self) -> None:
        """Stagger startup when multiple coordinators come up together.

        Simple policy: coordinator N waits N*5 seconds before its first refresh,
        where N is this entry's index among the domain's loaded entries. Avoids
        hammering the cloud API when the user has two accounts set up.
        """
        cfg_ids = [
            cfg.entry_id
            for cfg in self.hass.config_entries.async_entries(
                domain=DOMAIN, include_disabled=False
            )
        ]
        try:
            index = cfg_ids.index(self.config_entry.entry_id)
        except ValueError:
            index = 0
        delay = index * 5
        if delay:
            LOGGER.info(
                "Delaying coordinator %s for %s seconds to stagger data refresh",
                self.client.api.apisession.nickname,
                delay,
            )
            await sleep(delay)
