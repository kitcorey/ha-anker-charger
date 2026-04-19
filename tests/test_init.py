"""Tests for ``custom_components.anker_charger.__init__``."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch


from custom_components.anker_charger.const import (
    CONF_MQTT_OPTIONS,
    CONF_MQTT_USAGE,
    DOMAIN,
)


async def test_setup_entry_stores_coordinator(hass, setup_entry):
    """Successful setup should stash the coordinator on ``hass.data[DOMAIN]``."""
    assert setup_entry.entry_id in hass.data[DOMAIN]


async def test_setup_entry_forwards_platforms(hass, setup_entry):
    """Both A91B2 devices should end up in the device registry after setup."""
    from homeassistant.helpers import device_registry as dr

    reg = dr.async_get(hass)
    devices = {
        d.serial_number
        for d in dr.async_entries_for_config_entry(reg, setup_entry.entry_id)
    }
    assert "AFCJTB0F29104842" in devices
    assert "AFCJTB0F08102349" in devices


async def test_setup_entry_preregisters_account_device(hass, setup_entry):
    """Account device must exist before charger entities reference it via via_device.

    Without pre-registration, sensor platform setup (which runs before switch)
    adds charger entities whose via_device points at a not-yet-registered account
    device, triggering HA's "referencing a non existing via_device" warning.
    """
    from homeassistant.helpers import device_registry as dr

    from tests.conftest import ACCOUNT_EMAIL, CHARGER_SN_SUNROOM

    reg = dr.async_get(hass)
    account_dev = reg.async_get_device(identifiers={(DOMAIN, ACCOUNT_EMAIL)})
    assert account_dev is not None, "account device should be pre-registered at setup"
    assert setup_entry.entry_id in account_dev.config_entries

    charger_dev = reg.async_get_device(identifiers={(DOMAIN, CHARGER_SN_SUNROOM)})
    assert charger_dev is not None
    assert charger_dev.via_device_id == account_dev.id


async def test_unload_entry_removes_coordinator(hass, setup_entry):
    """Unloading the entry should free ``hass.data[DOMAIN]`` entry."""
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.entry_id not in hass.data[DOMAIN]


async def test_update_options_hot_patches_scan_interval(
    hass, mock_api_client, mock_config_entry
):
    """Changing just the scan interval should update the coordinator in place."""
    from custom_components.anker_charger.api_client import AnkerSolixApiClient

    with patch(
        "custom_components.anker_charger.api_client.AnkerSolixApiClient",
        return_value=mock_api_client,
    ) as cls_mock:
        cls_mock.scan_interval_from = AnkerSolixApiClient.scan_interval_from
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]
        assert coordinator.update_interval == timedelta(seconds=60)

        hass.config_entries.async_update_entry(
            mock_config_entry,
            options={
                **mock_config_entry.options,
                "scan_interval": 120,
            },
        )
        await hass.async_block_till_done()

        assert coordinator.update_interval == timedelta(seconds=120)


async def test_update_options_toggles_mqtt_and_reloads(
    hass, mock_api_client, mock_config_entry
):
    """Flipping the MQTT toggle should call ``mqtt_usage(enable=False)``."""
    from custom_components.anker_charger.api_client import AnkerSolixApiClient

    with patch(
        "custom_components.anker_charger.api_client.AnkerSolixApiClient",
        return_value=mock_api_client,
    ) as cls_mock:
        cls_mock.scan_interval_from = AnkerSolixApiClient.scan_interval_from
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Mock keeps returning True for the "current" poll; the new option is
        # False, so async_update_options should invoke mqtt_usage(enable=False).
        hass.config_entries.async_update_entry(
            mock_config_entry,
            options={
                **mock_config_entry.options,
                CONF_MQTT_OPTIONS: {CONF_MQTT_USAGE: False, "trigger_timeout": 300},
            },
        )
        await hass.async_block_till_done()

        enable_kwargs = [
            call.kwargs.get("enable")
            for call in mock_api_client.mqtt_usage.await_args_list
            if "enable" in call.kwargs
        ]
        assert False in enable_kwargs


async def test_remove_config_entry_device_blocks_active_device(
    hass, setup_entry, mock_api_client
):
    """Active (still-cached) devices should not be removable from the registry."""
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers import device_registry as dr

    from custom_components.anker_charger import async_remove_config_entry_device

    reg = dr.async_get(hass)
    dev = next(
        d
        for d in dr.async_entries_for_config_entry(reg, setup_entry.entry_id)
        if d.serial_number == "AFCJTB0F29104842"
    )
    entry: ConfigEntry = hass.config_entries.async_get_entry(setup_entry.entry_id)
    allowed = await async_remove_config_entry_device(hass, entry, dev)
    assert allowed is False


async def test_remove_config_entry_device_allows_orphan(
    hass, setup_entry, coordinator_data
):
    """A device whose SN is gone from the coordinator cache should be removable."""
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers import device_registry as dr

    from custom_components.anker_charger import async_remove_config_entry_device

    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    # Drop sunroom from the coordinator cache — it should now be removable.
    orphaned = coordinator.data.pop("AFCJTB0F29104842")
    reg = dr.async_get(hass)
    # The registry entry still references that SN.
    dev = next(
        d
        for d in dr.async_entries_for_config_entry(reg, setup_entry.entry_id)
        if d.serial_number == "AFCJTB0F29104842"
    )
    entry: ConfigEntry = hass.config_entries.async_get_entry(setup_entry.entry_id)
    allowed = await async_remove_config_entry_device(hass, entry, dev)
    assert allowed is True
    # Restore for isolation's sake (fixture scope is per-test but safer).
    coordinator.data["AFCJTB0F29104842"] = orphaned
