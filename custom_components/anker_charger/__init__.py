"""Anker A91B2 charger integration for Home Assistant."""

from __future__ import annotations

from datetime import timedelta

from aiohttp import ClientTimeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, CONF_USERNAME, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import (
    device_registry as dr,
    issue_registry as ir,
    restore_state,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from . import api_client
from .config_flow import async_check_and_remove_devices
from .const import (
    CONF_MQTT_OPTIONS,
    CONF_MQTT_USAGE,
    CONF_TRIGGER_TIMEOUT,
    DOMAIN,
    LOGGER,
    PLATFORMS,
    SHARED_ACCOUNT,
)
from .coordinator import AnkerSolixDataUpdateCoordinator
from .entity import get_AnkerSolixAccountInfo


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    username = entry.data.get(CONF_USERNAME)

    try:
        coordinator = AnkerSolixDataUpdateCoordinator(
            hass=hass,
            client=api_client.AnkerSolixApiClient(
                entry,
                session=async_create_clientsession(
                    hass, timeout=ClientTimeout(total=10)
                ),
            ),
            config_entry=entry,
            update_interval=api_client.AnkerSolixApiClient.scan_interval_from(entry),
        )
        if coordinator and coordinator.client:
            await coordinator.client.authenticate()
        await coordinator.async_refresh_delay()
        await coordinator.async_config_entry_first_refresh()
    except (
        api_client.AnkerSolixApiClientAuthenticationError,
        api_client.AnkerSolixApiClientRetryExceededError,
    ) as exception:
        raise ConfigEntryAuthFailed(exception) from exception

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    if shared_cfg := await async_check_and_remove_devices(
        hass=hass,
        user_input=entry.data,
        apidata=coordinator.data,
    ):
        # device is already registered for another account, abort configuration
        entry.async_cancel_retry_setup()
        ir.async_create_issue(
            hass,
            DOMAIN,
            "duplicate_devices",
            is_fixable=False,
            is_persistent=True,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.ERROR,
            translation_key="duplicate_devices",
            translation_placeholders={
                CONF_USERNAME: str(username),
                SHARED_ACCOUNT: str(shared_cfg.data.get("username")),
                CONF_NAME: str(shared_cfg.title),
            },
        )
        raise ConfigEntryError(
            api_client.AnkerSolixApiClientError(
                f"Found shared devices with {shared_cfg.title}"
            ),
            translation_key="duplicate_devices",
            translation_domain="config",
            translation_placeholders={
                CONF_USERNAME: str(username),
                SHARED_ACCOUNT: str(shared_cfg.data.get("username")),
                CONF_NAME: str(shared_cfg.title),
            },
        )

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Clear any stale duplicate-devices issue once this entry has loaded cleanly.
    entries = hass.config_entries.async_entries(DOMAIN, include_disabled=False)
    active = hass.data.get(DOMAIN) or []
    if len(active) >= len(entries):
        ir.async_delete_issue(hass, DOMAIN, "duplicate_devices")

    # Pre-register the account device so charger entities' via_device references
    # resolve during sensor platform setup (which runs before the switch platform
    # that would otherwise create this device implicitly).
    if username and (account_data := (coordinator.data or {}).get(username)):
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            **get_AnkerSolixAccountInfo(account_data, username),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options changes from the options flow.

    Only two fields are mutable: scan_interval and MQTT sub-options
    (mqtt_usage, trigger_timeout). Everything else is derived from the
    config entry at setup time.
    """
    coordinator: AnkerSolixDataUpdateCoordinator | None = hass.data[DOMAIN].get(
        entry.entry_id
    )
    if not coordinator or not coordinator.client:
        return

    mqtt = bool(
        entry.options.get(CONF_MQTT_OPTIONS, {}).get(
            CONF_MQTT_USAGE, api_client.DEFAULT_MQTT_USAGE
        )
    )
    trigger_timeout = int(
        entry.options.get(CONF_MQTT_OPTIONS, {}).get(
            CONF_TRIGGER_TIMEOUT, api_client.DEFAULT_TRIGGER_TIMEOUT
        )
    )
    seconds = int(
        entry.options.get(CONF_SCAN_INTERVAL, api_client.DEFAULT_UPDATE_INTERVAL)
    )

    # Apply hot updates in place without reloading the integration.
    if seconds != int(coordinator.update_interval.seconds):
        coordinator.update_interval = timedelta(seconds=seconds)
        LOGGER.info(
            "Api Coordinator %s update interval was changed to %s seconds",
            coordinator.config_entry.title,
            seconds,
        )
    coordinator.client.trigger_timeout(seconds=trigger_timeout)

    current_mqtt = await coordinator.client.mqtt_usage()
    if mqtt != current_mqtt:
        await coordinator.client.mqtt_usage(enable=mqtt)
        # Save restored state, then force a reload so entities pick up the new
        # MQTT-or-no-MQTT entity set.
        await restore_state.RestoreStateData.async_save_persistent_states(hass)
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an entry when the integration is removed or reloaded."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of a config entry — clear any lingering duplicate issue."""
    entries = hass.config_entries.async_entries(DOMAIN, include_disabled=False)
    active = hass.data.get(DOMAIN) or []
    if len(active) >= len(entries):
        ir.async_delete_issue(hass, DOMAIN, "duplicate_devices")


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removing a device only if it no longer appears in the coordinator cache."""
    coordinator: AnkerSolixDataUpdateCoordinator | None = hass.data[DOMAIN].get(
        config_entry.entry_id
    )
    if not coordinator:
        return True
    active = any(
        identifier
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
        for device_serial in coordinator.data
        if device_serial == identifier[1]
    )
    return not active
