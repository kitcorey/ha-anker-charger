"""Tests for the config flow + options flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries, data_entry_flow

from custom_components.anker_charger import api_client as ac_module
from custom_components.anker_charger.const import (
    CONF_MQTT_OPTIONS,
    CONF_MQTT_USAGE,
    DOMAIN,
)


async def test_user_form_shown(hass):
    """Starting the flow should render the credentials form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_flow_rejects_without_accepting_terms(hass, mock_api_client):
    """The form should bounce back with an error if terms aren't accepted."""
    with patch(
        "custom_components.anker_charger.config_flow.api_client.AnkerSolixApiClient",
        return_value=mock_api_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "username": "tester@example.com",
                "password": "pw",
                "country_code": "US",
                "accept_terms": False,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert "accept_terms" in result["errors"]


async def test_user_flow_auth_error_bounces(hass, mock_api_client):
    """An AuthenticationError during credentials step should show the ``auth`` error key."""
    mock_api_client.authenticate.side_effect = (
        ac_module.AnkerSolixApiClientAuthenticationError("nope")
    )
    with patch(
        "custom_components.anker_charger.config_flow.api_client.AnkerSolixApiClient",
        return_value=mock_api_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "username": "tester@example.com",
                "password": "wrong",
                "country_code": "US",
                "accept_terms": True,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["errors"] == {"base": "auth"}


async def test_user_flow_happy_path_creates_entry(hass, mock_api_client):
    """Happy path: credentials → options step → creates config entry."""
    with patch(
        "custom_components.anker_charger.config_flow.api_client.AnkerSolixApiClient",
        return_value=mock_api_client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "username": "tester@example.com",
                "password": "pw",
                "country_code": "US",
                "accept_terms": True,
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM
        assert result["step_id"] == "user_options"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "scan_interval": 60,
                CONF_MQTT_OPTIONS: {
                    CONF_MQTT_USAGE: True,
                    "trigger_timeout": 300,
                },
            },
        )
        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
        assert result["data"]["username"] == "tester@example.com"
        assert result["options"][CONF_MQTT_OPTIONS][CONF_MQTT_USAGE] is True
