"""Config flow for the Anker A91B2 charger integration."""

from __future__ import annotations

from typing import Any

from awesomeversion import AwesomeVersion
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_COUNTRY_CODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    __version__ as HAVERSION,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    restore_state,
    selector,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from . import api_client
from .const import (
    ACCEPT_TERMS,
    CONF_MQTT_OPTIONS,
    CONF_MQTT_USAGE,
    CONF_TRIGGER_TIMEOUT,
    DOMAIN,
    ERROR_DETAIL,
    LOGGER,
    MQ_LINK,
    MQTT_LINK,
    SHARED_ACCOUNT,
    TC_LINK,
    TERMS_LINK,
)

# Config entry versions. The schema format matches upstream's v2.1 so existing
# entries continue to load without a migration.
CONFIG_VERSION = 2
CONFIG_MINOR_VERSION = 1

# Defaults forwarded from api_client so the form shows sensible starting values.
SCAN_INTERVAL_DEF: int = api_client.DEFAULT_UPDATE_INTERVAL
TRIGGER_TIMEOUT_DEF: int = api_client.DEFAULT_TRIGGER_TIMEOUT
MQTT_USAGE_DEF: bool = api_client.DEFAULT_MQTT_USAGE

_SCAN_INTERVAL_MIN: int = 30
_SCAN_INTERVAL_MAX: int = 600
_SCAN_INTERVAL_STEP: int = 10
_TRIGGER_TIMEOUT_MIN: int = api_client.SolixDefaults.TRIGGER_TIMEOUT_MIN
_TRIGGER_TIMEOUT_MAX: int = api_client.SolixDefaults.TRIGGER_TIMEOUT_MAX
_TRIGGER_TIMEOUT_STEP: int = 10
_ACCEPT_TERMS: bool = False


class AnkerSolixFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for the Anker A91B2 charger integration."""

    VERSION = CONFIG_VERSION
    MINOR_VERSION = CONFIG_MINOR_VERSION

    def __init__(self) -> None:
        """Initialize empty state containers for the flow."""
        super().__init__()
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}
        self.client: api_client.AnkerSolixApiClient | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AnkerSolixOptionsFlowHandler:
        """Return the options flow handler."""
        return AnkerSolixOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Handle the initial config step."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {TERMS_LINK: TC_LINK}

        if user_input:
            if not user_input.get(ACCEPT_TERMS, ""):
                errors[ACCEPT_TERMS] = ACCEPT_TERMS
            else:
                account_user = user_input.get(CONF_USERNAME, "")
                try:
                    if await self.async_set_unique_id(account_user.lower()):
                        self._abort_if_unique_id_configured()
                    else:
                        self.client = await self._authenticate_client(user_input)

                    # Populate caches so async_check_and_remove_devices can see
                    # the devices the account owns before we commit the entry.
                    await self.client.api.update_device_details()
                    if cfg_entry := await async_check_and_remove_devices(
                        self.hass,
                        user_input,
                        self.client.api.getCaches(),
                    ):
                        errors[CONF_USERNAME] = "duplicate_devices"
                        placeholders[CONF_USERNAME] = str(account_user)
                        placeholders[SHARED_ACCOUNT] = str(cfg_entry.title)
                    else:
                        self._data = user_input
                        self._data["nickname"] = (
                            self.client.api.apisession.nickname or ""
                        )
                        return await self.async_step_user_options()

                except api_client.AnkerSolixApiClientAuthenticationError as exception:
                    LOGGER.warning(exception)
                    errors["base"] = "auth"
                    placeholders[ERROR_DETAIL] = str(exception)
                except api_client.AnkerSolixApiClientCommunicationError as exception:
                    LOGGER.error(exception)
                    errors["base"] = "connection"
                    placeholders[ERROR_DETAIL] = str(exception)
                except api_client.AnkerSolixApiClientRetryExceededError as exception:
                    LOGGER.error(exception)
                    errors["base"] = "exceeded"
                    placeholders[ERROR_DETAIL] = str(exception)
                except (api_client.AnkerSolixApiClientError, Exception) as exception:  # noqa: BLE001
                    LOGGER.error(exception)
                    errors["base"] = "unknown"
                    placeholders[ERROR_DETAIL] = (
                        f"Exception {type(exception)}: {exception}"
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(await self.get_config_schema(user_input or self._data)),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Forward reauth to the confirmation step."""
        return await self.async_step_reauth_confirm()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Forward reconfigure to the confirmation step."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Re-authenticate against cloud credentials, then reload the entry."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {TERMS_LINK: TC_LINK}
        config_entry: config_entries.ConfigEntry = (
            self.hass.config_entries.async_get_entry(self.context.get("entry_id"))
        )
        self._data = config_entry.data.copy()

        if user_input:
            if not user_input.get(ACCEPT_TERMS, ""):
                errors[ACCEPT_TERMS] = ACCEPT_TERMS
            else:
                account_user = user_input.get(CONF_USERNAME, "")
                try:
                    client = await self._authenticate_client(user_input)
                    await client.api.update_device_details()
                    if cfg_entry := await async_check_and_remove_devices(
                        hass=self.hass,
                        user_input=user_input,
                        apidata=client.api.getCaches(),
                        configured_user=self._data.get(CONF_USERNAME),
                    ):
                        errors[CONF_USERNAME] = "duplicate_devices"
                        placeholders[CONF_USERNAME] = str(account_user)
                        placeholders[SHARED_ACCOUNT] = str(cfg_entry.title)
                    else:
                        await async_check_and_remove_devices(
                            hass=self.hass,
                            user_input=self._data,
                            apidata={},
                        )
                        self._data.update(user_input)
                        self._data["nickname"] = client.api.apisession.nickname or ""
                        self.client = client
                        return self.async_update_reload_and_abort(
                            entry=config_entry,
                            unique_id=account_user,
                            title=self.client.api.apisession.nickname or account_user,
                            data=self._data,
                            reason="reconfig_successful",
                        )

                except api_client.AnkerSolixApiClientAuthenticationError as exception:
                    LOGGER.warning(exception)
                    errors["base"] = "auth"
                    placeholders[ERROR_DETAIL] = str(exception)
                except api_client.AnkerSolixApiClientCommunicationError as exception:
                    LOGGER.error(exception)
                    errors["base"] = "connection"
                    placeholders[ERROR_DETAIL] = str(exception)
                except api_client.AnkerSolixApiClientRetryExceededError as exception:
                    LOGGER.error(exception)
                    errors["base"] = "exceeded"
                    placeholders[ERROR_DETAIL] = str(exception)
                except (api_client.AnkerSolixApiClientError, Exception) as exception:  # noqa: BLE001
                    LOGGER.error(exception)
                    errors["base"] = "unknown"
                    placeholders[ERROR_DETAIL] = (
                        f"Exception {type(exception)}: {exception}"
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(await self.get_config_schema(user_input or self._data)),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_user_options(
        self, user_options: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Collect the minimal options (scan interval + MQTT) and create the entry."""
        placeholders: dict[str, str] = {MQTT_LINK: MQ_LINK}

        if user_options is not None:
            self._options = user_options
            return self.async_create_entry(
                title=self.client.api.apisession.nickname
                if self.client and self.client.api
                else self._data.get(CONF_USERNAME),
                data=self._data,
                options=self._options,
                description_placeholders=placeholders,
            )

        return self.async_show_form(
            step_id="user_options",
            data_schema=vol.Schema(get_options_schema(user_options or self._options)),
            description_placeholders=placeholders,
        )

    async def _authenticate_client(
        self, user_input: dict
    ) -> api_client.AnkerSolixApiClient:
        """Validate credentials and return the api client."""
        client = api_client.AnkerSolixApiClient(
            user_input,
            session=async_create_clientsession(self.hass),
        )
        await client.authenticate(restart=True)
        return client

    async def get_config_schema(self, entry: dict | None = None) -> dict:
        """Build the credentials form schema."""
        if entry is None:
            entry = {}
        return {
            vol.Required(
                CONF_USERNAME,
                default=entry.get(CONF_USERNAME),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.EMAIL, autocomplete="username"
                )
            ),
            vol.Required(
                CONF_PASSWORD,
                default=entry.get(CONF_PASSWORD),
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                ),
            ),
            vol.Required(
                CONF_COUNTRY_CODE,
                default=entry.get(CONF_COUNTRY_CODE) or self.hass.config.country,
            ): selector.CountrySelector(selector.CountrySelectorConfig()),
            vol.Required(
                ACCEPT_TERMS,
                default=entry.get(ACCEPT_TERMS, _ACCEPT_TERMS),
            ): selector.BooleanSelector(),
        }


class AnkerSolixOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow presenting just scan interval and MQTT settings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize the options flow handler."""
        if AwesomeVersion(HAVERSION) < "2024.11.99":
            self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Present the options form and persist user choices."""
        placeholders: dict[str, str] = {MQTT_LINK: MQ_LINK}
        existing_options = self.config_entry.options.copy()

        if user_input is not None:
            return self.async_create_entry(
                title="",
                data=existing_options | user_input,
                description_placeholders=placeholders,
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                get_options_schema(user_input or self.config_entry.options)
            ),
            description_placeholders=placeholders,
        )


def get_options_schema(entry: dict | None = None) -> dict:
    """Build the options schema used by both the config flow and options flow."""
    if entry is None:
        entry = {}
    mqtt_options = entry.get(CONF_MQTT_OPTIONS, {})
    mqtt_options_schema = {
        vol.Optional(
            CONF_MQTT_USAGE,
            default=mqtt_options.get(CONF_MQTT_USAGE, MQTT_USAGE_DEF),
        ): selector.BooleanSelector(),
        vol.Optional(
            CONF_TRIGGER_TIMEOUT,
            default=mqtt_options.get(CONF_TRIGGER_TIMEOUT, TRIGGER_TIMEOUT_DEF),
        ): vol.All(
            cv.positive_int,
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=_TRIGGER_TIMEOUT_MIN,
                    max=_TRIGGER_TIMEOUT_MAX,
                    step=_TRIGGER_TIMEOUT_STEP,
                    unit_of_measurement="sec",
                    mode=selector.NumberSelectorMode.SLIDER,
                ),
            ),
        ),
    }
    return {
        vol.Optional(
            CONF_SCAN_INTERVAL,
            default=entry.get(CONF_SCAN_INTERVAL, SCAN_INTERVAL_DEF),
        ): vol.All(
            cv.positive_int,
            selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=_SCAN_INTERVAL_MIN,
                    max=_SCAN_INTERVAL_MAX,
                    step=_SCAN_INTERVAL_STEP,
                    unit_of_measurement="sec",
                    mode=selector.NumberSelectorMode.BOX,
                ),
            ),
        ),
        vol.Required(CONF_MQTT_OPTIONS): section(
            vol.Schema(mqtt_options_schema),
            {"collapsed": True},
        ),
    }


async def async_check_and_remove_devices(
    hass: HomeAssistant,
    user_input: dict[str, Any],
    apidata: dict,
    configured_user: str | None = None,
) -> config_entries.ConfigEntry | None:
    """Detect accounts that would share devices, and prune orphaned device entries.

    For A91B2 chargers there are no sites and no complex category rules: each
    device SN either exists in the caller's apidata or it's orphaned.
    """
    obsolete_user_devs: dict[str, str] = {}

    cfg_entries = hass.config_entries.async_entries(domain=DOMAIN)
    for cfg_entry in cfg_entries:
        device_entries = dr.async_entries_for_config_entry(
            dr.async_get(hass), cfg_entry.entry_id
        )
        cfg_username = cfg_entry.data.get(CONF_USERNAME)
        for dev_entry in device_entries:
            username = str(user_input.get(CONF_USERNAME) or "")
            if username and username != cfg_username:
                # device registered by a different account — refuse if the SN overlaps
                if (
                    dev_entry.serial_number in apidata
                    and configured_user != cfg_username
                ):
                    return cfg_entry
            elif dev_entry.serial_number not in apidata:
                obsolete_user_devs[dev_entry.id] = dev_entry.serial_number

    if not configured_user and obsolete_user_devs:
        dev_registry = None
        await restore_state.RestoreStateData.async_save_persistent_states(hass)
        LOGGER.info("Saved HA states of restore entities prior removing devices")
        for dev_id, serial in obsolete_user_devs.items():
            if dev_registry is None:
                dev_registry = dr.async_get(hass)
            dev_registry.async_remove_device(dev_id)
            LOGGER.info(
                "Removed orphaned device entry %s (serial %s) from registry",
                dev_id,
                serial,
            )
    return None
