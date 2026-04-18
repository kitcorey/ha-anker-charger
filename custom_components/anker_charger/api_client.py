"""API client wrapper for the Anker A91B2 charger integration.

This fork only talks to A91B2 charging stations. Compared to upstream we no
longer need per-site polling, per-category exclusion lists, testmode
file-poller machinery, or the vehicle/export/backup-charge services — the
happy path is: login once, call get_bind_devices to populate the charger
cache, start the MQTT session for realtime data, keep MQTT alive on every
poll, done.
"""

from __future__ import annotations

from datetime import datetime
import socket

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_COUNTRY_CODE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
)

from .const import (
    CONF_MQTT_OPTIONS,
    CONF_MQTT_USAGE,
    CONF_TRIGGER_TIMEOUT,
    LOGGER,
)
from .solixapi import errors
from .solixapi.api import AnkerSolixApi
from .solixapi.apitypes import SolixDefaults
from .solixapi.mqtt_device import SolixMqttDevice
from .solixapi.mqtt_factory import SolixMqttDeviceFactory

_LOGGER = LOGGER

# Defaults.
DEFAULT_UPDATE_INTERVAL: int = 60  # seconds between cloud bind_devices refreshes
DEFAULT_TRIGGER_TIMEOUT: int = SolixDefaults.TRIGGER_TIMEOUT_DEF
DEFAULT_MQTT_USAGE: bool = True  # A91B2 is MQTT-only; enable by default
DEFAULT_ENDPOINT_LIMIT: int = SolixDefaults.ENDPOINT_LIMIT_DEF
DEFAULT_DELAY_TIME: float = SolixDefaults.REQUEST_DELAY_DEF
DEFAULT_TIMEOUT: int = SolixDefaults.REQUEST_TIMEOUT_DEF


class AnkerSolixApiClientError(Exception):
    """General API client error."""


class AnkerSolixApiClientCommunicationError(AnkerSolixApiClientError):
    """Communication error (network, timeout)."""


class AnkerSolixApiClientAuthenticationError(AnkerSolixApiClientError):
    """Authentication error (bad credentials, token rejected)."""


class AnkerSolixApiClientRetryExceededError(AnkerSolixApiClientError):
    """Retry budget exceeded on a login or request."""


class AnkerSolixApiClient:
    """HA-side wrapper around the trimmed AnkerSolixApi for A91B2 chargers."""

    last_device_refresh: datetime | None

    def __init__(
        self,
        entry: ConfigEntry | dict,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the API client from a config entry or plain dict."""
        data: dict = {}
        if isinstance(entry, ConfigEntry):
            if hasattr(entry, "data"):
                data.update(entry.data)
            if hasattr(entry, "options"):
                data.update(entry.options)
        else:
            data = entry

        self.api = AnkerSolixApi(
            data.get(CONF_USERNAME),
            data.get(CONF_PASSWORD),
            data.get(CONF_COUNTRY_CODE),
            session,
            _LOGGER,
        )
        if hasattr(entry, "title"):
            self.api.apisession.nickname = entry.title
        else:
            self.api.apisession.nickname = data.get("nickname", "")
        self.api.apisession.requestDelay(DEFAULT_DELAY_TIME)
        self.api.apisession.requestTimeout(DEFAULT_TIMEOUT)
        self.api.apisession.endpointLimit(DEFAULT_ENDPOINT_LIMIT)

        mqtt_options = data.get(CONF_MQTT_OPTIONS) or {}
        self._mqtt_usage: bool = bool(
            mqtt_options.get(CONF_MQTT_USAGE, DEFAULT_MQTT_USAGE)
        )
        self._trigger_timeout: int = int(
            mqtt_options.get(CONF_TRIGGER_TIMEOUT, DEFAULT_TRIGGER_TIMEOUT)
        )
        self._allow_refresh: bool = True
        self.mqtt_devices: dict[str, SolixMqttDevice] = {}
        self.last_device_refresh: datetime | None = None
        # keep the flag around so existing switch.py checks still resolve;
        # it's never set to False in this fork since there are no concurrent
        # device-detail pollers.
        self.active_device_refresh: bool = False

    # ------------------------------------------------------------------
    # Scan interval (persistent across option updates)
    # ------------------------------------------------------------------
    @staticmethod
    def scan_interval_from(entry: ConfigEntry | dict) -> int:
        """Return the scan interval from entry.options, or the default."""
        options = getattr(entry, "options", None) or entry
        return int(options.get(CONF_SCAN_INTERVAL, DEFAULT_UPDATE_INTERVAL))

    # ------------------------------------------------------------------
    # Test-mode stub (kept so downstream `coordinator.client.testmode()`
    # calls continue to return False). The fork no longer supports running
    # from a folder of JSON examples.
    # ------------------------------------------------------------------
    def testmode(self, mode: bool | None = None) -> bool:
        """No-op stub; this fork does not support test mode."""
        return False

    # ------------------------------------------------------------------
    # Authentication + raw request passthrough
    # ------------------------------------------------------------------
    async def authenticate(self, restart: bool = False) -> bool:
        """Authenticate against the Anker cloud and return a cached login status."""
        try:
            return await self.api.async_authenticate(restart=restart) or not restart
        except TimeoutError as exception:
            raise AnkerSolixApiClientCommunicationError(
                f"Timeout error fetching information: {exception}",
            ) from exception
        except (aiohttp.ClientError, socket.gaierror, errors.ConnectError) as exception:
            raise AnkerSolixApiClientCommunicationError(
                f"Api Connection Error: {exception}",
            ) from exception
        except (errors.AuthorizationError, errors.InvalidCredentialsError) as exception:
            raise AnkerSolixApiClientAuthenticationError(
                f"Authentication failed: {exception}",
            ) from exception
        except errors.RetryExceeded as exception:
            raise AnkerSolixApiClientRetryExceededError(
                f"Login Retries exceeded: {exception}",
            ) from exception
        except Exception as exception:  # pylint: disable=broad-except  # noqa: BLE001
            _LOGGER.exception("Api Client Exception:")
            raise AnkerSolixApiClientError(
                f"Api Client Error: {type(exception)}: {exception}"
            ) from exception

    # ------------------------------------------------------------------
    # Main refresh entry point used by the coordinator.
    # ------------------------------------------------------------------
    async def async_get_data(
        self,
        from_cache: bool = False,
        device_details: bool = False,
        vehicle_details: bool = False,
        reset_cache: bool = False,
    ) -> dict:
        """Refresh charger data and consolidated caches.

        Call path is simple for A91B2:
          * reset_cache=True → clear everything, MQTT will reconnect next poll
          * from_cache=True  → return consolidated cache without any cloud call
          * otherwise        → fetch bind_devices + ensure the MQTT session is up
        """
        if not self._allow_refresh:
            return {}

        try:
            if reset_cache:
                _LOGGER.debug(
                    "Api Coordinator %s is clearing Api cache",
                    self.api.apisession.nickname,
                )
                self.last_device_refresh = None
                self.api.clearCaches()
                self.mqtt_devices = {}

            if not from_cache:
                _LOGGER.debug(
                    "Api Coordinator %s is updating charger devices",
                    self.api.apisession.nickname,
                )
                await self.api.update_device_details()
                await self.check_mqtt_session()
                self.last_device_refresh = datetime.now().astimezone()
                _LOGGER.debug(
                    "Api Coordinator %s request statistics: %s",
                    self.api.apisession.nickname,
                    self.api.request_count,
                )

            data = self.api.getCaches()
            _LOGGER.debug("Coordinator %s data: %s", self.api.apisession.nickname, data)
            return data

        except TimeoutError as exception:
            raise AnkerSolixApiClientCommunicationError(
                f"Timeout error fetching information: {exception}",
            ) from exception
        except (aiohttp.ClientError, socket.gaierror, errors.ConnectError) as exception:
            raise AnkerSolixApiClientCommunicationError(
                f"Api Connection Error: {exception}",
            ) from exception
        except (errors.AuthorizationError, errors.InvalidCredentialsError) as exception:
            raise AnkerSolixApiClientAuthenticationError(
                f"Authentication failed: {exception}",
            ) from exception
        except errors.RetryExceeded as exception:
            raise AnkerSolixApiClientRetryExceededError(
                f"Retries exceeded: {exception}",
            ) from exception
        except Exception as exception:  # pylint: disable=broad-except  # noqa: BLE001
            _LOGGER.exception("Api Client Exception:")
            raise AnkerSolixApiClientError(
                f"Api Client Error: {type(exception)}: {exception}"
            ) from exception

    # ------------------------------------------------------------------
    # Allow-refresh toggle (driven by the `allow_refresh` account switch).
    # ------------------------------------------------------------------
    def allow_refresh(self, allow: bool | None = None) -> bool:
        """Query or toggle the cloud-polling refresh flag."""
        if allow is not None and allow != self._allow_refresh:
            self._allow_refresh = allow
            _LOGGER.info(
                "Api Coordinator %s refresh was %s",
                self.api.apisession.nickname,
                "ENABLED" if allow else "DISABLED",
            )
        return self._allow_refresh

    # ------------------------------------------------------------------
    # MQTT lifecycle
    # ------------------------------------------------------------------
    async def mqtt_usage(self, enable: bool | None = None) -> bool:
        """Query or toggle MQTT usage, starting/stopping the session as needed."""
        if (
            enable is not None
            and isinstance(enable, bool)
            and enable != self._mqtt_usage
        ):
            _LOGGER.info(
                "Api Coordinator %s MQTT usage was changed from %s to %s",
                self.api.apisession.nickname,
                self._mqtt_usage,
                enable,
            )
            self._mqtt_usage = enable
            if enable:
                await self.check_mqtt_session()
            else:
                self.api.stopMqttSession()
                self.mqtt_devices.clear()
        return self._mqtt_usage

    def trigger_timeout(self, seconds: int | None = None) -> int:
        """Query or set the MQTT realtime-trigger timeout (seconds)."""
        if (
            seconds is not None
            and isinstance(seconds, float | int)
            and (seconds := round(seconds)) != self._trigger_timeout
        ):
            _LOGGER.info(
                "Api Coordinator %s MQTT trigger timeout was changed from %s to %s seconds",
                self.api.apisession.nickname,
                self._trigger_timeout,
                seconds,
            )
            self._trigger_timeout = seconds
        return self._trigger_timeout

    def get_mqtt_device(self, sn: str) -> SolixMqttDevice | None:
        """Return the MQTT device wrapper for a serial, or None."""
        return (isinstance(sn, str) and self.mqtt_devices.get(sn)) or None

    def get_mqtt_devices(
        self,
        siteId: str | None = None,
        stationSn: str | None = None,
        extraDeviceSn: str | None = None,
        mqttControl: str | None = None,
    ) -> list[SolixMqttDevice]:
        """Filter the live MQTT device instances by site/station/serial/control."""
        return [
            md
            for md in self.mqtt_devices.values()
            if (not mqttControl or mqttControl in md.controls)
            and (
                md.sn == extraDeviceSn
                or (
                    (siteId is None or md.device.get("site_id") == siteId)
                    and (stationSn is None or md.device.get("station_sn") == stationSn)
                )
            )
        ]

    def get_mqtt_valuecount(self, sn: str | None = None) -> int:
        """Count cached MQTT values across all devices or a single serial."""
        count = 0
        for mdev in self.mqtt_devices.values():
            count += len(mdev.mqttdata) if (not sn or sn == mdev.sn) else 0
        return count

    async def check_mqtt_session(self) -> None:
        """Ensure the MQTT session is up if usage is enabled."""
        if not self._mqtt_usage:
            return

        if not self.api.mqttsession or not self.api.mqttsession.is_connected():
            _LOGGER.info(
                "Api Coordinator %s is (re-)starting MQTT session",
                self.api.apisession.nickname,
            )
            if not await self.api.startMqttSession():
                _LOGGER.error(
                    "Api Coordinator %s failed to start MQTT session",
                    self.api.apisession.nickname,
                )
                self.mqtt_devices.clear()
                return

            mqtt_devs = [
                dev
                for dev in self.api.devices.values()
                if dev.get("mqtt_supported")
            ]
            _LOGGER.info(
                "Api Coordinator %s MQTT session connected, subscribing eligible devices",
                self.api.apisession.nickname,
            )
            for dev in mqtt_devs:
                self.subscribe_device(dev)
            if not mqtt_devs:
                _LOGGER.warning(
                    "Api Coordinator %s did not find eligible devices for MQTT subscription",
                    self.api.apisession.nickname,
                )
            for dev in mqtt_devs:
                sn = dev.get("device_sn")
                if sn and (
                    mdev := SolixMqttDeviceFactory(
                        api_instance=self.api, device_sn=sn
                    ).create_device()
                ):
                    self.mqtt_devices[sn] = mdev
            for mdev in self.mqtt_devices.values():
                if mdev.device.get("mqtt_status_request"):
                    await mdev.status_request()
                await mdev.realtime_trigger(timeout=self._trigger_timeout)
        else:
            # Already connected: re-subscribe anything that lost its subscription
            # and keep the 0303 realtime stream alive.
            for mdev in self.mqtt_devices.values():
                if not mdev.is_subscribed() and not self.subscribe_device(
                    mdev.device
                ):
                    mdev.mqttdata.clear()
            for mdev in self.mqtt_devices.values():
                await mdev.realtime_trigger(timeout=self._trigger_timeout)

    def subscribe_device(self, deviceDict: dict) -> bool:
        """Subscribe a device to MQTT messages."""
        if not self.api.mqttsession or not self.api.mqttsession.is_connected():
            return False
        topic = f"{self.api.mqttsession.get_topic_prefix(deviceDict=deviceDict)}#"
        resp = self.api.mqttsession.subscribe(topic)
        if resp and resp.is_failure:
            _LOGGER.warning(
                "Api Coordinator %s failed subscription for MQTT topic: %s",
                self.api.apisession.nickname,
                topic,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Test helpers kept no-op so lingering callers compile.
    # ------------------------------------------------------------------
    def toggle_cache(self, toggle: bool) -> None:
        """No-op; cache is always valid in this fork."""

    async def validate_cache(self, timeout: int = 10) -> bool:
        """No-op; cache is always valid in this fork."""
        return True

    # ------------------------------------------------------------------
    # Raw request passthrough — retained for anything that still calls it.
    # ------------------------------------------------------------------
    async def request(
        self, method: str, endpoint: str, payload: dict | None = None
    ) -> dict:
        """Issue a raw request to the Anker cloud API through the apisession."""
        try:
            return await self.api.apisession.request(
                method=method,
                endpoint=endpoint,
                json=payload,
            )
        except TimeoutError as exception:
            raise AnkerSolixApiClientCommunicationError(
                f"Timeout error fetching information: {exception}",
            ) from exception
        except (aiohttp.ClientError, socket.gaierror, errors.ConnectError) as exception:
            raise AnkerSolixApiClientCommunicationError(
                f"Api Connection Error: {exception}",
            ) from exception
        except (errors.AuthorizationError, errors.InvalidCredentialsError) as exception:
            raise AnkerSolixApiClientAuthenticationError(
                f"Authentication failed: {exception}",
            ) from exception
        except errors.RetryExceeded as exception:
            raise AnkerSolixApiClientRetryExceededError(
                f"Login Retries exceeded: {exception}",
            ) from exception
        except Exception as exception:  # pylint: disable=broad-except  # noqa: BLE001
            _LOGGER.exception("Api Client Exception:")
            raise AnkerSolixApiClientError(
                f"Api Client Error: {type(exception)}: {exception}"
            ) from exception

    # Dummy kept so coordinator.py's vehicle branch (dead but not yet pruned)
    # still resolves on import until Phase 6 cleanup.
    def get_registered_vehicles(self) -> list:
        """Stub: vehicles are not supported in this fork."""
        return []
